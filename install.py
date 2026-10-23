#!/usr/bin/env python3
"""install.py — install the `ctmux` wrapper.

  macOS  symlink bin/ctmux (the shell script) onto PATH, advertise the agent
         skill, and the login LaunchAgent ONLY when this machine opts in via
         tools.items.ctmux.enabled: true in ~/.config/rig/config.yaml
         (default off, a standard tools.items.<name> key). Nothing is
         brew-installed: a missing tmux / fzf / cmux is only a note.
  Linux  tmux + fzf (apt, or Homebrew when apt is not usable), symlink
         linux/ctmux onto PATH, a terminal desktop entry (ctmux.desktop, runs
         `ctmux terminal`), advertise the agent skill. No cmux, osascript or
         LaunchAgent: ctmux runs on tmux alone there.

Usage: ./install.sh [--check] [--dry-run]
  --check    change nothing; exit 0 only when everything is installed
             correctly (what rig / provisioning scripts verify with)
  --dry-run  print what would be done, change nothing
Both are safe to combine. Re-running is idempotent: only what is missing or
stale is touched.

Run it through install.sh, which also handles the no-checkout (curl) case.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import List, Optional, Tuple

REPO = "ctmux-cli"
GITHUB_USER = "alex-mextner"
ENTRIES = {"Darwin": "bin/ctmux", "Linux": "linux/ctmux"}
LAUNCH_AGENT_LABEL = "com.ultra.ctmux-ensure"
TEMPLATE_BIN = "/Users/ultra/.local/bin/ctmux"
TEMPLATE_CMUX_CFG = "/Users/ultra/.config/cmux"

HOME = os.environ.get("HOME") or os.path.expanduser("~")
RIG_CFG = os.path.join(HOME, ".config/rig/config.yaml")
LAUNCH_AGENT_DEST = os.path.join(HOME, "Library/LaunchAgents", LAUNCH_AGENT_LABEL + ".plist")
BIN = os.path.join(HOME, ".local/bin")
SKILLS_DIR = os.path.join(HOME, ".agents/skills")
SKILL_DEST = os.path.join(SKILLS_DIR, "ctmux/SKILL.md")
BLURB_DEST = os.path.join(SKILLS_DIR, ".blurbs/ctmux.md")
SRC = os.path.dirname(os.path.abspath(__file__))
DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local/share")
APPS_DIR = os.path.join(DATA_HOME, "applications")
DESKTOP_DEST = os.path.join(APPS_DIR, "ctmux.desktop")


def out(msg: str = "") -> None:
    print(msg, flush=True)


def die(msg: str) -> None:
    print("ERROR: " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def exec_quote(path: str) -> str:
    """A path as one argument of a .desktop Exec line (Desktop Entry spec, "The Exec key"):
    quoted when it holds a reserved character, with ", `, $ and \\ escaped (and the backslashes
    doubled again for the key-file string syntax), % as %%."""
    if not any(c in " \t\n\"'\\><~|&;$*?#()`" for c in path):
        return path.replace("%", "%%")
    out = ""
    for c in path:
        if c in '"`$':
            out += "\\\\" + c
        elif c == "\\":
            out += "\\\\\\\\"
        elif c == "%":
            out += "%%"
        else:
            out += c
    return '"' + out + '"'


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def capture(cmd: List[str]) -> str:
    """stdout of an external command ('' when it cannot be run)."""
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return ""
    return res.stdout.decode("utf-8", "replace").strip()


def run_ok(cmd: List[str], quiet: bool = False) -> bool:
    sink = subprocess.DEVNULL if quiet else None
    try:
        return subprocess.run(cmd, stdout=sink, stderr=sink).returncode == 0
    except OSError:
        return False


def run_or_exit(cmd: List[str]) -> None:
    try:
        rc = subprocess.run(cmd).returncode
    except OSError as exc:
        print("ERROR: %s: %s" % (cmd[0], exc), file=sys.stderr, flush=True)
        sys.exit(1)
    if rc != 0:
        sys.exit(rc)


def rig_ctmux_enabled(path: str) -> bool:
    """tools.items.ctmux.enabled from a block-style YAML file (true/yes/on).

    Minimal indentation-based reader: nested `key:` mappings and `key: value`
    scalars are all rig's config needs here. Anything unreadable is false.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    stack: List[Tuple[int, str]] = []  # (indent, key) of open mappings
    enabled = False
    for raw in lines:
        text = raw.split(" #", 1)[0].rstrip()
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        indent = len(text) - len(text.lstrip(" "))
        key, sep, value = text.strip().partition(":")
        if not sep:
            continue
        key = key.strip().strip("'\"")
        value = value.strip().strip("'\"")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if value == "":
            stack.append((indent, key))
        elif [k for _, k in stack] + [key] == ["tools", "items", "ctmux", "enabled"]:
            enabled = value.lower() in ("true", "yes", "on")
    return enabled


class Installer:
    def __init__(self, check: bool, dry: bool) -> None:
        self.check = check
        self.dry = dry
        self.problems = 0
        self.sudo = ""
        self.os_name = ""

    # --check bookkeeping: every check prints one line, bad ones are counted.
    def ok(self, msg: str) -> None:
        out("ctmux: ok: " + msg)

    def bad(self, msg: str) -> None:
        out("ctmux: MISSING: " + msg)
        self.problems += 1

    def act(self, what: str, fn) -> None:
        """Run fn() for real, or just say what would be done under --dry-run."""
        if self.dry:
            out("ctmux: would " + what)
        else:
            fn()

    def detect_os(self) -> None:
        name = capture(["uname", "-s"])
        if name not in ("Darwin", "Linux"):
            die("unsupported OS '%s': ctmux runs on macOS and Linux." % name)
        self.os_name = name

    def can_sudo(self) -> bool:
        """Sets self.sudo to "" (root) or "sudo"; False when privileges can't
        be had without a password prompt nobody can answer."""
        if capture(["id", "-u"]) == "0":
            return True
        if not have("sudo"):
            return False
        if run_ok(["sudo", "-n", "true"], quiet=True) or sys.stdin.isatty():
            self.sudo = "sudo"
            return True
        return False

    def pkg_install(self, pkgs: List[str]) -> None:
        names = " ".join(pkgs)
        if have("apt-get") and self.can_sudo():
            apt = ([self.sudo] if self.sudo else []) + ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get"]
            if self.dry:
                out("ctmux: would %s install -y %s" % (" ".join(apt), names))
                return
            install = apt + ["install", "-y"] + pkgs
            if subprocess.run(install).returncode != 0:
                if subprocess.run(apt + ["update"]).returncode != 0:
                    sys.exit(1)
                rc = subprocess.run(install).returncode
                if rc != 0:
                    sys.exit(rc)
        elif have("brew"):
            self.act("brew install " + names, lambda: run_or_exit(["brew", "install"] + pkgs))
        elif have("apt-get"):
            die("cannot install %s: apt needs root and neither sudo nor Homebrew is usable "
                "without a password prompt — run: sudo apt install %s" % (names, names))
        else:
            die("cannot install %s: no apt-get and no Homebrew found — install them with "
                "your package manager (Linux), then re-run." % names)

    def dependencies(self) -> None:
        if self.os_name == "Darwin":
            self.darwin_dependency_notes()
            return
        missing: List[str] = []
        for cmd in ("tmux", "fzf"):
            found = shutil.which(cmd)
            if found:
                if self.check:
                    self.ok("%s (%s)" % (cmd, found))
            elif self.check:
                self.bad(cmd + " not on PATH")
            else:
                missing.append(cmd)
        if missing:
            out("ctmux: missing dependencies: " + " ".join(missing))
            self.pkg_install(missing)

    def darwin_dependency_notes(self) -> None:
        """macOS installs nothing: a missing tool is a note, never a failure."""
        for cmd in ("tmux", "fzf"):
            found = shutil.which(cmd)
            if found:
                if self.check:
                    self.ok("%s (%s)" % (cmd, found))
            else:
                out("ctmux: note: %s not found (brew install %s)" % (cmd, cmd))
        cmux_installed = (
            have("cmux")
            or os.path.isdir("/Applications/cmux.app")
            or os.path.isdir(os.path.join(HOME, "Applications/cmux.app"))
        )
        if cmux_installed:
            if self.check:
                self.ok("cmux app")
        else:
            out("ctmux: note: cmux app not found (brew install --cask cmux)")

    def link_entry(self) -> None:
        target = os.path.join(SRC, ENTRIES[self.os_name])
        link = os.path.join(BIN, "ctmux")
        try:
            current: Optional[str] = os.readlink(link)
        except OSError:
            current = None
        correct = current == target and os.access(target, os.X_OK)
        if self.check:
            if correct:
                self.ok("%s -> %s" % (link, target))
            else:
                self.bad("%s -> %s" % (link, target))
        elif not (self.dry and correct):
            self.act("mkdir -p " + BIN, lambda: os.makedirs(BIN, exist_ok=True))
            if not os.access(target, os.X_OK):
                self.act("chmod +x " + target, lambda: os.chmod(target, os.stat(target).st_mode | 0o111))

            def relink() -> None:
                if os.path.lexists(link):
                    os.remove(link)
                os.symlink(target, link)

            self.act("ln -sf %s %s" % (target, link), relink)
            if not self.dry:
                out("ctmux: symlinked %s -> %s" % (link, target))

        if BIN not in os.environ.get("PATH", "").split(":") and not self.check:
            out("")
            out("  NOTE: %s is not on your PATH." % BIN)
            if self.os_name == "Darwin":
                out("  Add the following line to your ~/.zshrc and restart your shell:")
            else:
                out("  Add the following line to your ~/.profile (or ~/.bashrc / ~/.zshrc) and restart your shell:")
            out('    export PATH="%s:$PATH"' % BIN)
            out("")

    def desktop_entry(self) -> None:
        """Linux: ~/.local/share/applications/ctmux.desktop, a terminal entry whose Exec is
        `ctmux terminal` (absolute path: the desktop session's PATH may lack ~/.local/bin).
        Making it THE terminal is not done here: list `ctmux.desktop` first in
        ~/.config/xdg-terminals.list (xdg-terminal-exec, Ctrl+Alt+T on GNOME) — a file a
        dotfiles profile can declare."""
        with open(os.path.join(SRC, "linux/ctmux.desktop"), encoding="utf-8") as fh:
            wanted = fh.read().replace("@CTMUX@", exec_quote(os.path.join(BIN, "ctmux")))
        self.sync_text(wanted, DESKTOP_DEST, "terminal entry")

    def sync_text(self, wanted: str, dest: str, what: str) -> None:
        """Make dest hold `wanted`: ok / MISSING under --check, written otherwise."""
        try:
            with open(dest, encoding="utf-8") as fh:
                same = fh.read() == wanted
        except OSError:
            same = False
        if same:
            if self.check:
                self.ok(dest)
        elif self.check:
            self.bad("%s (%s; run install.sh)" % (dest, what))
        else:
            parent = os.path.dirname(dest)

            def write() -> None:
                os.makedirs(parent, exist_ok=True)
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(wanted)

            self.act("write " + dest, write)

    # rig treats a tool as provisioned only when its skill blurb exists
    # (~/.agents/skills/.blurbs/<tool>.md); without it rig re-runs this script
    # on every apply. The files are copies, refreshed whenever they differ.
    def advertise(self, src: str, dest: str) -> None:
        try:
            with open(src, "rb") as a, open(dest, "rb") as b:
                same = a.read() == b.read()
        except OSError:
            same = False
        if same:
            if self.check:
                self.ok(dest)
        elif self.check:
            self.bad(dest + " (agent skill; run install.sh)")
        else:
            parent = os.path.dirname(dest)
            self.act("mkdir -p " + parent, lambda: os.makedirs(parent, exist_ok=True))
            self.act("copy %s -> %s" % (src, dest), lambda: shutil.copy(src, dest))

    def render_plist(self) -> None:
        template = os.path.join(SRC, "launchd", LAUNCH_AGENT_LABEL + ".plist")
        cmux_cfg = os.path.join(HOME, ".config/cmux")
        with open(template, encoding="utf-8", newline="") as fh:
            lines = fh.readlines()
        # sed 's#a#b#' semantics: first occurrence per line only.
        rendered = [
            ln.replace(TEMPLATE_BIN, os.path.join(BIN, "ctmux"), 1).replace(TEMPLATE_CMUX_CFG, cmux_cfg, 1)
            for ln in lines
        ]
        with open(LAUNCH_AGENT_DEST, "w", encoding="utf-8", newline="") as fh:
            fh.writelines(rendered)

    @staticmethod
    def plist_mentions_link() -> bool:
        try:
            with open(LAUNCH_AGENT_DEST, encoding="utf-8", errors="replace") as fh:
                return os.path.join(BIN, "ctmux") in fh.read()
        except OSError:
            return False

    def launch_agent(self) -> None:
        # Off by default: tools.items.ctmux.enabled in rig's own config.yaml —
        # a supported per-item key (never under rig's closed tmux block).
        enabled = rig_ctmux_enabled(RIG_CFG)
        agents_dir = os.path.dirname(LAUNCH_AGENT_DEST)
        domain = "gui/" + capture(["id", "-u"])
        if self.check:
            if enabled:
                if os.path.isfile(LAUNCH_AGENT_DEST) and self.plist_mentions_link():
                    self.ok("login LaunchAgent " + LAUNCH_AGENT_DEST)
                else:
                    self.bad("login LaunchAgent %s (enabled in %s)" % (LAUNCH_AGENT_DEST, RIG_CFG))
            elif os.path.lexists(LAUNCH_AGENT_DEST):
                self.bad("login LaunchAgent %s should be removed (not enabled in %s)" % (LAUNCH_AGENT_DEST, RIG_CFG))
            else:
                self.ok("login LaunchAgent not wanted (tools.items.ctmux.enabled is not true)")
        elif enabled:
            cfg_dir = os.path.join(HOME, ".config/cmux")

            def mkdirs() -> None:
                os.makedirs(agents_dir, exist_ok=True)
                os.makedirs(cfg_dir, exist_ok=True)

            self.act("mkdir -p %s %s" % (agents_dir, cfg_dir), mkdirs)
            if self.dry:
                out("ctmux: would render %s/launchd/%s.plist -> %s and launchctl bootstrap it"
                    % (SRC, LAUNCH_AGENT_LABEL, LAUNCH_AGENT_DEST))
            else:
                self.render_plist()
                run_ok(["launchctl", "bootout", domain, LAUNCH_AGENT_DEST], quiet=True)
                run_or_exit(["launchctl", "bootstrap", domain, LAUNCH_AGENT_DEST])
                out("ctmux: tools.items.ctmux.enabled=true in %s — login LaunchAgent installed and running." % RIG_CFG)
        else:
            self.act("mkdir -p " + agents_dir, lambda: os.makedirs(agents_dir, exist_ok=True))
            if self.dry:
                out("ctmux: would launchctl bootout and remove " + LAUNCH_AGENT_DEST)
            else:
                run_ok(["launchctl", "bootout", domain, LAUNCH_AGENT_DEST], quiet=True)
                try:
                    os.remove(LAUNCH_AGENT_DEST)
                except FileNotFoundError:
                    pass
            out("ctmux: tools.items.ctmux.enabled is not true in %s — login LaunchAgent not installed." % RIG_CFG)
            out("       Enable it per-machine by adding to %s:" % RIG_CFG)
            out("         tools:")
            out("           items:")
            out("             ctmux:")
            out("               repo: ~/xp/ctmux-cli")
            out("               enabled: true")
            out("       then re-run this install.sh.")

    def run(self) -> int:
        self.detect_os()
        if not self.check:
            out("ctmux: using local clone at " + SRC)
        self.dependencies()
        self.link_entry()
        self.advertise(os.path.join(SRC, "skill/SKILL.md"), SKILL_DEST)
        self.advertise(os.path.join(SRC, "skill/blurb.md"), BLURB_DEST)
        if self.os_name == "Darwin":
            self.launch_agent()
        else:
            self.desktop_entry()

        if self.check:
            if self.problems == 0:
                out("ctmux: installed correctly (%s)" % self.os_name)
                return 0
            out("ctmux: NOT installed correctly: %d problem(s) — run %s/install.sh" % (self.problems, SRC))
            return 1

        out("")
        out("  Dry run: nothing was changed." if self.dry else "  ctmux is installed.")
        if self.os_name == "Darwin":
            out("  ctmux            ensure the mirror is up, launch/focus cmux")
        else:
            out("  ctmux            ensure the canonical tmux session exists, attach to it")
        out("  ctmux ls         fzf picker over session:window pairs")
        if self.os_name == "Darwin":
            out("  ctmux new [name] create a new tmux session and mirror it")
        else:
            out("  ctmux new [name] create a new tmux session and open it")
        out("  Docs: %s/README.md" % SRC)
        out("")
        return 0


def main(argv: List[str]) -> int:
    check = dry = False
    for arg in argv:
        if arg == "--check":
            check = True
        elif arg == "--dry-run":
            dry = True
        elif arg in ("-h", "--help"):
            out((__doc__ or "").split("\n", 1)[1].strip("\n"))
            return 0
        else:
            print("install.sh: unknown option '%s' (try --check, --dry-run)" % arg, file=sys.stderr, flush=True)
            return 2
    return Installer(check, dry).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
