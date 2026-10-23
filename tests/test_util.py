"""The process helper, the launcher and the repo's own hygiene rules."""
import ast
import os
import subprocess
import sys
import tempfile
import unittest

from world import LINUX, ROOT

sys.path.insert(0, LINUX)
from ctmux_linux import util  # noqa: E402
from ctmux_linux.util import Stdio  # noqa: E402

SH = "/bin/sh"


class RunTest(unittest.TestCase):
    def test_captures_stdout(self):
        self.assertEqual(util.run([SH, "-c", "echo out; echo err >&2"], stdout=Stdio.PIPE, stderr=Stdio.DEVNULL), (0, "out\n"))

    def test_merges_stderr_into_stdout(self):
        rc, text = util.run([SH, "-c", "echo out; echo err >&2; exit 3"], stdout=Stdio.PIPE, stderr=Stdio.MERGE)
        self.assertEqual(rc, 3)
        self.assertEqual(sorted(text.split()), ["err", "out"])

    def test_feeds_stdin(self):
        self.assertEqual(util.run(["cat"], stdin="a\nb\n", stdout=Stdio.PIPE), (0, "a\nb\n"))

    def test_large_stdin_does_not_deadlock(self):
        data = "x" * 200000 + "\n"
        rc, text = util.run(["cat"], stdin=data, stdout=Stdio.PIPE)
        self.assertEqual((rc, len(text)), (0, len(data)))

    def test_missing_command_is_127_like_a_shell(self):
        self.assertEqual(util.run(["definitely-not-a-command-xyz"]), (127, ""))

    def test_signal_death_is_128_plus_signal(self):
        self.assertEqual(util.run([SH, "-c", "kill -TERM $$"])[0], 128 + 15)

    def test_exit_status_passes_through(self):
        self.assertEqual(util.run([SH, "-c", "exit 7"])[0], 7)

    def test_which_skips_directories_and_non_executables(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "adir"))
            open(os.path.join(d, "plain"), "w").close()
            old = os.environ["PATH"]
            os.environ["PATH"] = d
            try:
                self.assertIsNone(util.which("adir"))
                self.assertIsNone(util.which("plain"))
            finally:
                os.environ["PATH"] = old


class LauncherTest(unittest.TestCase):
    def test_launcher_is_tiny_and_runs_through_a_symlink(self):
        launcher = os.path.join(LINUX, "ctmux")
        with open(launcher) as f:
            self.assertLessEqual(len(f.read().splitlines()), 10)
        with tempfile.TemporaryDirectory() as d:
            link = os.path.join(d, "ctmux")
            os.symlink(launcher, link)
            for argv in ([link], [launcher]):
                p = subprocess.run(argv + ["frobnicate"], capture_output=True, text=True)
                self.assertEqual((p.returncode, p.stderr), (1, "ctmux: unknown command 'frobnicate' (try: ls, new [name], here [dir], ensure, terminal, or no args)\n"))


class HygieneTest(unittest.TestCase):
    """Owner policy: rig-ecosystem tools are Python (new shell glue <= 40 lines);
    linux/ctmux_linux + install.py must run on Python 3.9 with the stdlib alone.
    (bin/ctmux and tests/ctmux_test.sh are the pre-policy macOS shell original,
    deliberately untouched.)"""

    def sources(self, *dirs):
        for d in dirs:
            for name in sorted(os.listdir(os.path.join(ROOT, d))):
                if name.endswith(".py"):
                    yield os.path.join(ROOT, d, name)

    LEGACY_SHELL = ("bin/ctmux", "tests/ctmux_test.sh")

    def test_shell_scripts_stay_under_the_policy_limit(self):
        for dirpath, dirnames, files in os.walk(ROOT):
            dirnames[:] = [x for x in dirnames if x not in (".git", "__pycache__")]
            for name in files:
                path = os.path.join(dirpath, name)
                if os.path.relpath(path, ROOT) in self.LEGACY_SHELL:
                    continue
                with open(path, "rb") as f:
                    head = f.read(64)
                if name.endswith(".sh") or head.startswith((b"#!/bin/sh", b"#!/bin/bash", b"#!/usr/bin/env bash")):
                    with open(path) as f:
                        self.assertLessEqual(len(f.read().splitlines()), 40, path)

    def test_package_parses_as_python_3_9(self):
        for path in list(self.sources("linux/ctmux_linux")) + [os.path.join(ROOT, "install.py")]:
            with open(path) as f:
                ast.parse(f.read(), path, feature_version=(3, 9))

    def test_package_imports_only_the_stdlib(self):
        stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
        for path in self.sources("linux/ctmux_linux"):
            with open(path) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    if stdlib is not None:
                        self.assertIn(m, stdlib, "%s imports non-stdlib %s" % (path, m))

    def test_hot_path_modules_do_not_import_heavy_stdlib(self):
        """`ctmux ensure` on the tmux backend must not pay for json/re/subprocess/tempfile."""
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from ctmux_linux import cli\n"
            "from ctmux_linux.tmux_backend import Tmux\n"
            "bad = sorted(m for m in ('json', 're', 'subprocess', 'tempfile', 'secrets', 'shutil', 'datetime') if m in sys.modules)\n"
            "print(','.join(bad))\n"
        ) % LINUX
        p = subprocess.run([sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True)
        self.assertEqual(p.stdout.strip(), "", p.stderr)


if __name__ == "__main__":
    unittest.main()
