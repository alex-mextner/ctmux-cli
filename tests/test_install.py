"""Black-box tests for install.sh / install.py against a fake world.

PATH is hermetic: a per-test stub dir (uname, apt-get, sudo, brew, launchctl,
id, git, and tmux/fzf/cmux when a test says they are installed) followed by a
dir of symlinks to just the system tools the shim and the stubs use — so what
is "installed" is decided by the test, never by the machine running it. HOME
is a per-test fixture dir: nothing outside it is ever written, and the real
~/.local, ~/.config, ~/.agents and ~/Library are never read.

Run: python3 -m unittest discover -s tests -p 'test_install.py' -v
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import importlib.util
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH = shutil.which("bash") or "/bin/bash"
SYS_TOOLS = ["bash", "dirname", "env", "mkdir", "chmod", "ln", "cp", "cat", "rm", "true"]
PLIST = "com.ultra.ctmux-ensure.plist"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def same_file(a: str, b: str) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.W = tempfile.mkdtemp(prefix="ctmux-install-test.")
        self.addCleanup(self.cleanup)
        self.bin = os.path.join(self.W, "bin")
        self.sysdir = os.path.join(self.W, "sys")
        self.home = os.path.join(self.W, "home")
        for d in (self.bin, self.sysdir, self.home):
            os.mkdir(d)
        self.write(os.path.join(self.W, "calls"), "")
        for tool in SYS_TOOLS:
            path = shutil.which(tool)
            if path:
                os.symlink(path, os.path.join(self.sysdir, tool))
        os.symlink(sys.executable, os.path.join(self.sysdir, "python3"))
        self.write(os.path.join(self.W, "uname_s"), "Linux\n")
        self.write(os.path.join(self.W, "uid"), "1000\n")
        self.quiet_stub("uname", 'IFS= read -r u <"$W/uname_s"; echo "$u"')
        self.quiet_stub("id", 'IFS= read -r u <"$W/uid"; echo "$u"')
        # launchctl is only ever reached on the Darwin path.
        self.stub("launchctl", "exit 0")
        self.stub("osascript", "exit 0")
        self.make_checkout()
        self.rc = -1
        self.out = ""
        self.err = ""

    def cleanup(self) -> None:
        subprocess.call(["chmod", "-R", "u+w", self.W], stderr=subprocess.DEVNULL)
        shutil.rmtree(self.W, ignore_errors=True)

    def make_checkout(self) -> None:
        """A checkout of the installer under test inside the temp dir: the
        real installer, skill and plist template, and non-executable stand-ins
        for the two entry scripts (the installer must chmod +x them). The real
        repo files are never touched."""
        self.repo = os.path.join(self.W, "repo")
        os.makedirs(os.path.join(self.repo, "bin"))
        os.makedirs(os.path.join(self.repo, "linux"))  # before the linux/ files below are copied in
        for name in ("install.sh", "install.py"):
            shutil.copyfile(os.path.join(ROOT, name), os.path.join(self.repo, name))
        shutil.copytree(os.path.join(ROOT, "skill"), os.path.join(self.repo, "skill"))
        shutil.copytree(os.path.join(ROOT, "launchd"), os.path.join(self.repo, "launchd"))
        shutil.copyfile(os.path.join(ROOT, "linux", "ctmux.desktop"), os.path.join(self.repo, "linux", "ctmux.desktop"))
        for entry in ("bin/ctmux", "linux/ctmux"):
            self.write(os.path.join(self.repo, entry), "#!/bin/sh\nexit 0\n", 0o644)

    # ---------- fake world ----------

    @staticmethod
    def write(path: str, content: str, mode: int = 0o644) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(path, mode)

    def stub(self, name: str, body: str) -> None:
        """A logging stub: every call is appended to $W/calls."""
        self.write(
            os.path.join(self.bin, name),
            '#!/bin/bash\necho "%s $*" >>"$W/calls"\n%s\n' % (name, body),
            0o755,
        )

    def quiet_stub(self, name: str, body: str) -> None:
        """Answers like a command but is not part of the call log."""
        self.write(os.path.join(self.bin, name), "#!/bin/bash\n%s\n" % body, 0o755)

    def have_cmd(self, *cmds: str) -> None:
        """Commands that are already installed."""
        for c in cmds:
            self.write(os.path.join(self.bin, c), "#!/bin/bash\nexit 0\n", 0o755)

    def pkg_manager(self, name: str, nargs: int) -> None:
        """Installs 'work': they drop an installed stub per package into the stub dir."""
        self.stub(
            name,
            'shift %d; for p in "$@"; do case $p in -*) ;; *) '
            'printf "#!/bin/bash\\nexit 0\\n" >"$W/bin/$p"; chmod +x "$W/bin/$p" ;; esac; done' % nargs,
        )

    def apt_works(self) -> None:
        self.pkg_manager("apt-get", 1)  # apt-get install -y PKG...

    def sudo_works(self) -> None:
        self.stub("sudo", '[ "${1:-}" = -n ] && shift; exec "$@"')

    def brew_works(self) -> None:
        self.pkg_manager("brew", 1)  # brew install [--cask] PKG...

    def run_install(self, *args: str, install: str = "", stdin=subprocess.DEVNULL) -> None:
        env = {
            "HOME": self.home,
            "PATH": "%s:%s" % (self.bin, self.sysdir),
            "XDG_DATA_HOME": os.path.join(self.home, ".local/share"),
            "W": self.W,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        res = subprocess.run(
            [BASH, install or os.path.join(self.repo, "install.sh")] + list(args),
            env=env,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.rc = res.returncode
        self.out = res.stdout.decode("utf-8", "replace")
        self.err = res.stderr.decode("utf-8", "replace")

    def calls(self) -> str:
        return read(os.path.join(self.W, "calls"))

    def call_lines(self) -> list:
        return self.calls().splitlines()

    def home_files(self) -> list:
        found = []
        for base, dirs, files in os.walk(self.home):
            found += [os.path.join(base, n) for n in dirs + files]
        return sorted(found)

    def link_path(self) -> str:
        return os.path.join(self.home, ".local/bin/ctmux")

    def installed_link(self, entry: str = "linux/ctmux") -> bool:
        try:
            return os.readlink(self.link_path()) == os.path.join(self.repo, entry)
        except OSError:
            return False

    def blurb(self) -> str:
        return os.path.join(self.home, ".agents/skills/.blurbs/ctmux.md")

    def assertCalled(self, line: str) -> None:
        self.assertIn(line, self.call_lines(), "calls: " + self.calls())

    def assertNoCall(self, prefix: str) -> None:
        bad = [c for c in self.call_lines() if c.startswith(prefix)]
        self.assertEqual(bad, [], "calls: " + self.calls())

    def assertRc(self, want: int) -> None:
        self.assertEqual(self.rc, want, "exit %d: err=%s out=%s" % (self.rc, self.err, self.out))

    # ---------- Linux ----------

    def test_linux_dry_run_changes_nothing(self) -> None:
        self.have_cmd("tmux")
        self.apt_works()
        self.sudo_works()
        self.run_install("--dry-run")
        self.assertRc(0)
        self.assertRegex(self.out, r"would .*apt-get install -y fzf")
        self.assertIn("would ln -sf %s/linux/ctmux %s" % (self.repo, self.link_path()), self.out)
        self.assertEqual(self.home_files(), [])
        for prefix in ("apt-get", "brew", "launchctl", "osascript"):
            self.assertNoCall(prefix)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "fzf")), "fzf got installed")

    def test_linux_install_uses_apt_and_stays_off_macos_tools(self) -> None:
        self.have_cmd("tmux")
        self.apt_works()
        self.sudo_works()
        self.run_install()
        self.assertRc(0)
        self.assertCalled("apt-get install -y fzf")
        self.assertEqual(sum(c.startswith("apt-get") for c in self.call_lines()), 1, self.calls())
        self.assertTrue(self.installed_link("linux/ctmux"), "symlink missing/wrong")
        self.assertTrue(os.access(os.path.join(self.repo, "linux/ctmux"), os.X_OK), "linux/ctmux not made executable")
        self.assertTrue(
            same_file(
                os.path.join(self.repo, "skill/SKILL.md"), os.path.join(self.home, ".agents/skills/ctmux/SKILL.md")
            ),
            "SKILL.md not advertised",
        )
        self.assertTrue(same_file(os.path.join(self.repo, "skill/blurb.md"), self.blurb()), "blurb not advertised")
        for prefix in ("brew", "launchctl", "osascript"):
            self.assertNoCall(prefix)
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")), "created ~/Library on Linux")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config")), "created ~/.config on Linux")

    def test_linux_root_runs_apt_directly(self) -> None:
        self.have_cmd("tmux")
        self.apt_works()
        self.write(os.path.join(self.W, "uid"), "0\n")
        self.run_install()
        self.assertRc(0)
        self.assertCalled("apt-get install -y fzf")
        self.assertNoCall("sudo")

    def test_linux_second_run_is_a_noop(self) -> None:
        self.have_cmd("tmux")
        self.apt_works()
        self.sudo_works()
        self.run_install()
        self.write(os.path.join(self.W, "calls"), "")
        self.run_install()
        self.assertRc(0)
        self.assertEqual(self.calls(), "", "second run did something")
        self.run_install("--check")
        self.assertRc(0)
        self.assertIn("installed correctly (Linux)", self.out)

    def test_linux_installs_nothing_when_deps_are_present(self) -> None:
        self.have_cmd("tmux", "fzf")
        self.apt_works()
        self.sudo_works()
        self.run_install()
        self.assertRc(0)
        self.assertNoCall("apt-get")
        self.assertNoCall("brew")

    def test_linux_apt_update_retry_when_lists_are_stale(self) -> None:
        self.have_cmd("tmux")
        self.sudo_works()
        self.stub(
            "apt-get",
            'if [ "$1" = update ]; then : >"$W/updated"; exit 0; fi; [ -e "$W/updated" ] || exit 100; '
            'printf "#!/bin/bash\\nexit 0\\n" >"$W/bin/fzf"; chmod +x "$W/bin/fzf"',
        )
        self.run_install()
        self.assertRc(0)
        self.assertCalled("apt-get update")
        self.assertTrue(os.access(os.path.join(self.bin, "fzf"), os.X_OK), "fzf not installed after the retry")

    def test_linux_brew_is_the_fallback_without_privileges(self) -> None:
        self.have_cmd("tmux")
        self.apt_works()
        self.brew_works()
        self.run_install()
        self.assertRc(0)
        self.assertCalled("brew install fzf")
        self.assertNoCall("apt-get")

    def test_linux_no_way_to_install_names_the_command(self) -> None:
        self.have_cmd("tmux")
        self.apt_works()
        self.run_install()
        self.assertRc(1)
        self.assertIn("sudo apt install fzf", self.err)

    def test_linux_check_reports_every_problem_and_changes_nothing(self) -> None:
        self.run_install("--check")
        self.assertRc(1)
        self.assertIn("MISSING: tmux not on PATH", self.out)
        self.assertIn("MISSING: fzf not on PATH", self.out)
        self.assertIn("MISSING: %s" % self.link_path(), self.out)
        self.assertRegex(self.out, r"MISSING: .*blurbs/ctmux.md")
        self.assertEqual(self.calls(), "", "--check ran commands")
        self.assertEqual(self.home_files(), [], "--check wrote files")

    def test_linux_check_catches_drift_after_a_good_install(self) -> None:
        self.have_cmd("tmux", "fzf")
        self.run_install()
        self.run_install("--check")
        self.assertRc(0)
        os.remove(self.link_path())
        self.run_install("--check")
        self.assertRc(1)
        self.run_install()
        self.write(self.blurb(), "stale\n")
        self.run_install("--check")
        self.assertRc(1)
        self.run_install()
        self.assertTrue(same_file(os.path.join(self.repo, "skill/blurb.md"), self.blurb()), "stale blurb not refreshed")
        os.remove(os.path.join(self.bin, "fzf"))
        self.run_install("--check")
        self.assertRc(1)

    def desktop_entry(self) -> str:
        return os.path.join(self.home, ".local/share/applications/ctmux.desktop")

    def test_linux_installs_a_terminal_desktop_entry_with_an_absolute_exec(self) -> None:
        self.have_cmd("tmux", "fzf")
        self.run_install()
        self.assertRc(0)
        entry = read(self.desktop_entry())
        self.assertIn("Exec=%s terminal\n" % self.link_path(), entry)
        self.assertIn("TerminalEmulator", entry)
        self.assertNotIn("@CTMUX@", entry)

    def test_linux_check_catches_a_missing_or_stale_desktop_entry(self) -> None:
        self.have_cmd("tmux", "fzf")
        self.run_install()
        os.remove(self.desktop_entry())
        self.run_install("--check")
        self.assertRc(1)
        self.assertIn("ctmux.desktop", self.out)
        self.run_install()
        self.write(self.desktop_entry(), "[Desktop Entry]\nExec=old\n")
        self.run_install("--check")
        self.assertRc(1)
        self.run_install()
        self.run_install("--check")
        self.assertRc(0)

    def test_exec_path_with_reserved_characters_is_quoted(self) -> None:
        spec = importlib.util.spec_from_file_location("ctmux_install", os.path.join(ROOT, "install.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.exec_quote("/home/u/.local/bin/ctmux"), "/home/u/.local/bin/ctmux")
        self.assertEqual(mod.exec_quote("/home/a b/bin/ctmux"), '"/home/a b/bin/ctmux"')
        self.assertEqual(mod.exec_quote("/h/50%/ctmux"), "/h/50%%/ctmux")
        self.assertEqual(mod.exec_quote('/h/a"b/x'), '"/h/a\\\\"b/x"')

    def test_darwin_does_not_get_a_desktop_entry(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "Darwin\n")
        self.have_cmd("tmux", "fzf")
        self.run_install()
        self.assertFalse(os.path.exists(self.desktop_entry()))

    def test_linux_check_catches_a_link_to_another_checkout(self) -> None:
        self.have_cmd("tmux", "fzf")
        self.run_install()
        os.remove(self.link_path())
        os.symlink("/somewhere/else/bin/ctmux", self.link_path())
        self.run_install("--check")
        self.assertRc(1)
        self.run_install()
        self.assertTrue(self.installed_link(), "install did not repoint the link")

    def test_unsupported_os_is_refused(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "FreeBSD\n")
        self.run_install()
        self.assertRc(1)
        self.assertIn("unsupported OS 'FreeBSD'", self.err)

    def test_unknown_option_is_a_usage_error(self) -> None:
        self.run_install("--frobnicate")
        self.assertRc(2)
        self.assertIn("unknown option '--frobnicate'", self.err)

    def test_clone_url_points_at_the_real_owner(self) -> None:
        """Piped from curl (no checkout next to the shim) it clones THIS repo's real URL."""
        self.have_cmd("tmux", "fzf")
        solo = os.path.join(self.W, "solo")
        os.mkdir(solo)
        shutil.copy(os.path.join(ROOT, "install.sh"), os.path.join(solo, "install.sh"))
        self.stub("git", 'if [ "$1" = clone ]; then mkdir -p "$3" && cp -R "%s/." "$3"; fi' % self.repo)
        self.run_install(install=os.path.join(solo, "install.sh"))
        self.assertRc(0)
        clone = os.path.join(self.home, ".local/share/ctmux-cli")
        self.assertCalled("git clone https://github.com/alex-mextner/ctmux-cli.git " + clone)
        self.assertEqual(os.readlink(self.link_path()), clone + "/linux/ctmux", "link not into the clone")

    def test_help_prints_usage(self) -> None:
        self.run_install("--help")
        self.assertRc(0)
        self.assertIn("--check", self.out)
        self.assertIn("--dry-run", self.out)

    # ---------- macOS ----------

    def test_darwin_install_notes_missing_cmux_and_removes_an_unwanted_launch_agent(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "Darwin\n")
        self.have_cmd("tmux", "fzf")
        self.brew_works()
        agents = os.path.join(self.home, "Library/LaunchAgents")
        os.makedirs(agents)
        self.write(os.path.join(agents, PLIST), "")
        self.run_install()
        self.assertRc(0)
        self.assertNoCall("brew")
        self.assertIn("ctmux: note: cmux app not found (brew install --cask cmux)", self.out)
        self.assertTrue(any(c.startswith("launchctl bootout") for c in self.call_lines()), self.calls())
        self.assertFalse(os.path.exists(os.path.join(agents, PLIST)), "unwanted plist left behind")
        self.assertIn("login LaunchAgent not installed", self.out)
        self.assertTrue(self.installed_link("bin/ctmux"), "symlink missing/wrong")
        self.assertTrue(os.access(os.path.join(self.repo, "bin/ctmux"), os.X_OK), "bin/ctmux not made executable")

    def test_darwin_missing_tools_are_notes_not_failures(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "Darwin\n")
        self.run_install()
        self.assertRc(0)
        self.assertIn("ctmux: note: tmux not found (brew install tmux)", self.out)
        self.assertIn("ctmux: note: fzf not found (brew install fzf)", self.out)
        self.assertIn("ctmux: note: cmux app not found (brew install --cask cmux)", self.out)
        self.assertNoCall("brew")

    def test_darwin_enabled_in_rig_config_installs_the_launch_agent(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "Darwin\n")
        self.have_cmd("tmux", "fzf", "cmux")
        os.makedirs(os.path.join(self.home, ".config/rig"))
        self.write(
            os.path.join(self.home, ".config/rig/config.yaml"),
            "tools:\n  items:\n    ctmux:\n      enabled: true\n",
        )
        self.run_install()
        self.assertRc(0)
        plist = os.path.join(self.home, "Library/LaunchAgents", PLIST)
        text = read(plist)
        self.assertIn(self.link_path(), text, "plist not pointed at the symlink")
        self.assertNotIn("/Users/ultra", text, "plist still has the template's home")
        self.assertTrue(any(c.startswith("launchctl bootstrap") for c in self.call_lines()), self.calls())
        self.run_install("--check")
        self.assertRc(0)
        os.remove(plist)
        self.run_install("--check")
        self.assertRc(1)

    def test_darwin_dry_run_changes_nothing(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "Darwin\n")
        self.have_cmd("tmux", "fzf")
        self.brew_works()
        self.run_install("--dry-run")
        self.assertRc(0)
        self.assertIn("would ln -sf %s/bin/ctmux %s" % (self.repo, self.link_path()), self.out)
        self.assertEqual(self.home_files(), [])
        self.assertNoCall("brew")
        self.assertNoCall("launchctl")

    def test_darwin_check_only_notes_the_missing_cmux_app(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "Darwin\n")
        self.have_cmd("tmux", "fzf")
        self.run_install()
        self.run_install("--check")
        self.assertRc(0)
        self.assertIn("ctmux: note: cmux app not found", self.out)
        self.assertNotIn("MISSING", self.out)

    def test_darwin_check_catches_a_link_to_the_linux_entry(self) -> None:
        self.write(os.path.join(self.W, "uname_s"), "Darwin\n")
        self.run_install()
        os.remove(self.link_path())
        os.symlink(os.path.join(self.repo, "linux/ctmux"), self.link_path())
        self.run_install("--check")
        self.assertRc(1)
        self.assertIn("MISSING: %s -> %s/bin/ctmux" % (self.link_path(), self.repo), self.out)

    # ---------- shim / Python-port extras ----------

    def test_shim_is_at_most_40_lines(self) -> None:
        self.assertLessEqual(len(read(os.path.join(ROOT, "install.sh")).splitlines()), 40)

    def test_shim_without_checkout_refuses_dry_run_and_check(self) -> None:
        solo = os.path.join(self.W, "solo")
        os.mkdir(solo)
        shutil.copy(os.path.join(ROOT, "install.sh"), os.path.join(solo, "install.sh"))
        self.stub("git", "exit 0")
        for flag in ("--dry-run", "--check"):
            self.run_install(flag, install=os.path.join(solo, "install.sh"))
            self.assertRc(1)
            self.assertIn("no ctmux checkout", self.err)
        self.assertEqual(self.calls(), "", "shim cloned despite --dry-run/--check")
        self.assertEqual(self.home_files(), [])

    def test_install_py_parses_as_python_39(self) -> None:
        source = read(os.path.join(ROOT, "install.py"))
        ast.parse(source, filename="install.py", feature_version=(3, 9))

    def test_rig_config_reader_needs_no_pyyaml(self) -> None:
        spec = importlib.util.spec_from_file_location("ctmux_install_under_test", os.path.join(ROOT, "install.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        base = "tools:\n  items:\n    ctmux:\n%s\n"
        cases = [
            ("      enabled: true", True),
            ("      enabled: yes", True),
            ("      enabled: ON", True),
            ("      enabled: True  # opt in", True),
            ("      enabled: 'true'", True),
            ("      enabled: false", False),
            ("      enabled: no", False),
            ("      repo: ~/xp/ctmux-cli", False),
        ]
        for line, want in cases:
            path = os.path.join(self.W, "config.yaml")
            self.write(path, base % line)
            self.assertEqual(mod.rig_ctmux_enabled(path), want, line)
        other = "tools:\n  items:\n    other:\n      enabled: true\n    ctmux:\n      repo: x\n"
        self.write(os.path.join(self.W, "config.yaml"), other)
        self.assertFalse(mod.rig_ctmux_enabled(os.path.join(self.W, "config.yaml")), "other item leaked")
        self.write(os.path.join(self.W, "config.yaml"), "enabled: true\ntmux:\n  enabled: true\n")
        self.assertFalse(mod.rig_ctmux_enabled(os.path.join(self.W, "config.yaml")), "wrong key path accepted")
        self.assertFalse(mod.rig_ctmux_enabled(os.path.join(self.W, "missing.yaml")), "missing file is not enabled")


if __name__ == "__main__":
    unittest.main()
