#!/usr/bin/env python3
"""Record the macOS install trace of the ORIGINAL shell install.sh.

    git show HEAD:install.sh > /tmp/install.orig.sh
    python3 tests/golden/generate_install.py /tmp/install.orig.sh

Each scenario in SCENARIOS is run against the old script in a hermetic world
(PATH = stub dir + a dir of symlinks to the few system tools the script uses;
HOME = a fixture dir; `launchctl` is a stub that only logs its argv). Per run
it records the exit code, the normalized stdout and the ordered stub calls;
per scenario it records the resulting HOME tree and the exec bit of
bin/ctmux. The result goes to tests/golden/install_trace.json, which
tests/test_install_parity.py replays against the new install.sh + install.py.

The old script reads rig's config through PyYAML. This python has it; when it
does not, a tiny fake `yaml.safe_load` is put on PYTHONPATH for the OLD script
only (recorded in the trace as "yaml": "fake").

This module is also the shared harness for the parity test: build_checkout,
run_scenario and SCENARIOS are imported from there.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TRACE_PATH = os.path.join(HERE, "install_trace.json")
PLIST = "com.ultra.ctmux-ensure.plist"
PLIST_REL = "Library/LaunchAgents/" + PLIST
SYS_TOOLS = ["bash", "dirname", "env", "mkdir", "chmod", "ln", "cp", "cat", "rm", "sed", "readlink", "true"]

ENABLED = "tools:\n  items:\n    ctmux:\n      enabled: true\n"
DISABLED = "tools:\n  items:\n    ctmux:\n      enabled: false\n"
ITEM_MISSING = "tools:\n  items:\n    other:\n      enabled: true\n"
ENABLED_YES = "tools:\n  items:\n    ctmux:\n      enabled: yes\n"
GARBAGE = "tools: [unclosed\n"
STALE_PLIST = "<plist>stale /Users/ultra/.local/bin/ctmux</plist>\n"

# A step is ("config", yaml) | ("file", home_rel, text) | ("link", home_rel,
# target) | ("run", bin_on_path).
Step = Tuple
SCENARIOS: Dict[str, List[Step]] = {
    "fresh_no_config": [("run", False)],
    "fresh_no_config_bin_on_path": [("run", True)],
    "enabled_true": [("config", ENABLED), ("run", False)],
    "enabled_true_bin_on_path": [("config", ENABLED), ("run", True)],
    "enabled_yes": [("config", ENABLED_YES), ("run", False)],
    "enabled_false": [("config", DISABLED), ("run", False)],
    "item_missing": [("config", ITEM_MISSING), ("run", False)],
    "config_garbage": [("config", GARBAGE), ("run", False)],
    "rerun_idempotent": [("run", False), ("run", False)],
    "rerun_enabled_idempotent": [("config", ENABLED), ("run", False), ("run", False)],
    "foreign_symlink": [("link", ".local/bin/ctmux", "/somewhere/else/bin/ctmux"), ("run", False)],
    "stale_plist_disabled": [("file", PLIST_REL, STALE_PLIST), ("config", DISABLED), ("run", False)],
    "stale_plist_enabled": [("file", PLIST_REL, STALE_PLIST), ("config", ENABLED), ("run", False)],
    "enabled_then_disabled": [("config", ENABLED), ("run", False), ("config", DISABLED), ("run", False)],
}

FAKE_YAML = '''\
"""Minimal stand-in for yaml.safe_load: nested block mappings, bool scalars."""
def safe_load(stream):
    root, stack = {}, [(-1, {})]
    stack[0] = (-1, root)
    for raw in stream.read().splitlines():
        text = raw.rstrip()
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        indent = len(text) - len(text.lstrip())
        key, sep, value = text.strip().partition(":")
        if not sep:
            raise ValueError("not a mapping")
        while stack[-1][0] >= indent:
            stack.pop()
        value = value.strip()
        if value == "":
            child = {}
            stack[-1][1][key] = child
            stack.append((indent, child))
        elif value.startswith("["):
            raise ValueError("flow sequence not supported")
        else:
            stack[-1][1][key] = value.lower() in ("true", "yes", "on")
    return root
'''


def have_pyyaml() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def write(path: str, content: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    os.chmod(path, mode)


def build_checkout(dest: str, installer_files: Dict[str, str]) -> None:
    """The repo files the installer needs, plus `installer_files`
    (checkout-relative path -> source path or directory). bin/ctmux starts
    non-executable so the chmod +x is observable."""
    for rel in ("bin/ctmux", "README.md"):
        os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), os.path.join(dest, rel))
    os.chmod(os.path.join(dest, "bin/ctmux"), 0o644)
    shutil.copytree(os.path.join(ROOT, "launchd"), os.path.join(dest, "launchd"))
    for rel, src in installer_files.items():
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isdir(src):
            shutil.copytree(src, target)
        else:
            shutil.copyfile(src, target)


class World:
    """Hermetic fake world under one temp dir."""

    def __init__(self, base: str) -> None:
        self.W = os.path.realpath(base)
        self.bin = os.path.join(self.W, "bin")
        self.sysdir = os.path.join(self.W, "sys")
        self.home = os.path.join(self.W, "home")
        self.src = os.path.join(self.W, "checkout")
        for d in (self.bin, self.sysdir, self.home, self.src):
            os.mkdir(d)
        write(os.path.join(self.W, "calls"), "")
        for tool in SYS_TOOLS:
            path = shutil.which(tool)
            if path:
                os.symlink(path, os.path.join(self.sysdir, tool))
        os.symlink(sys.executable, os.path.join(self.sysdir, "python3"))
        write(os.path.join(self.bin, "uname"), "#!/bin/bash\necho Darwin\n", 0o755)
        write(os.path.join(self.bin, "id"), "#!/bin/bash\necho 501\n", 0o755)
        write(os.path.join(self.bin, "launchctl"), '#!/bin/bash\necho "launchctl $*" >>"$W/calls"\nexit 0\n', 0o755)

    def norm(self, text: str) -> str:
        for real, token in ((self.src, "$SRC"), (self.home, "$HOME"), (self.W, "$W")):
            text = text.replace(real, token)
        return re.sub(r"gui/\d+", "gui/<uid>", text)

    def calls(self) -> List[str]:
        with open(os.path.join(self.W, "calls"), encoding="utf-8") as fh:
            return [self.norm(line) for line in fh.read().splitlines()]

    def tree(self) -> List[Dict[str, str]]:
        entries: List[Dict[str, str]] = []

        def walk(directory: str) -> None:
            for name in sorted(os.listdir(directory)):
                path = os.path.join(directory, name)
                rel = os.path.relpath(path, self.home)
                if os.path.islink(path):
                    entries.append({"path": rel, "kind": "symlink", "target": self.norm(os.readlink(path))})
                elif os.path.isdir(path):
                    entries.append({"path": rel, "kind": "dir"})
                    walk(path)
                else:
                    entry = {"path": rel, "kind": "file"}
                    if rel == PLIST_REL:
                        with open(path, encoding="utf-8", newline="") as fh:
                            entry["content"] = self.norm(fh.read())
                    entries.append(entry)

        walk(self.home)
        return entries

    def bin_ctmux_executable(self) -> bool:
        return bool(os.stat(os.path.join(self.src, "bin/ctmux")).st_mode & stat.S_IXUSR)


def run_scenario(
    steps: List[Step],
    build: Callable[[str], None],
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """Run `steps` in a fresh world whose checkout is populated by build(src);
    returns the normalized observations."""
    with tempfile.TemporaryDirectory(prefix="ctmux-install-golden.") as base:
        world = World(base)
        build(world.src)
        runs: List[Dict[str, object]] = []
        seen_calls = 0
        for step in steps:
            kind = step[0]
            if kind == "config":
                write(os.path.join(world.home, ".config/rig/config.yaml"), step[1])
            elif kind == "file":
                write(os.path.join(world.home, step[1]), step[2])
            elif kind == "link":
                link = os.path.join(world.home, step[1])
                os.makedirs(os.path.dirname(link), exist_ok=True)
                os.symlink(step[2], link)
            else:
                path = [world.bin] + ([os.path.join(world.home, ".local/bin")] if step[1] else []) + [world.sysdir]
                env = {
                    "HOME": world.home,
                    "PATH": ":".join(path),
                    "XDG_DATA_HOME": os.path.join(world.home, ".local/share"),
                    "W": world.W,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONIOENCODING": "utf-8",
                }
                env.update(extra_env or {})
                res = subprocess.run(
                    ["bash", os.path.join(world.src, "install.sh")],
                    env=env,
                    cwd=world.W,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                calls = world.calls()
                runs.append(
                    {
                        "rc": res.returncode,
                        "stdout": world.norm(res.stdout.decode("utf-8", "replace")).splitlines(),
                        "calls": calls[seen_calls:],
                    }
                )
                seen_calls = len(calls)
        return {"runs": runs, "tree": world.tree(), "bin_ctmux_executable": world.bin_ctmux_executable()}


def main(argv: List[str]) -> int:
    if len(argv) != 1:
        print("usage: generate_install.py PATH_TO_OLD_install.sh", file=sys.stderr)
        return 2
    old = os.path.abspath(argv[0])
    extra_env: Dict[str, str] = {}
    yaml_kind = "pyyaml"
    fake_dir = tempfile.mkdtemp(prefix="ctmux-fake-yaml.")
    try:
        if not have_pyyaml():
            yaml_kind = "fake"
            write(os.path.join(fake_dir, "yaml.py"), FAKE_YAML)
            extra_env["PYTHONPATH"] = fake_dir
        scenarios = {
            name: run_scenario(steps, lambda src: build_checkout(src, {"install.sh": old}), extra_env)
            for name, steps in SCENARIOS.items()
        }
    finally:
        shutil.rmtree(fake_dir, ignore_errors=True)
    trace = {"recorded_from": "git show HEAD:install.sh (original shell installer)", "yaml": yaml_kind, "scenarios": scenarios}
    with open(TRACE_PATH, "w", encoding="utf-8") as fh:
        json.dump(trace, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print("wrote %s (%d scenarios, yaml=%s)" % (TRACE_PATH, len(scenarios), yaml_kind))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
