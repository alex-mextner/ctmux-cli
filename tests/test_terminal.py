"""`ctmux terminal` (the desktop's terminal entry) and the tmux key bindings ctmux loads.

Emulators are shell stubs that record their argv; the key-binding tests drive a REAL tmux on a
private socket through a pty with the byte sequences Ghostty's ctmux config sends.
"""
import contextlib
import os
import pty
import select
import shutil
import subprocess
import sys
import time
import unittest

from test_tmux_backend import LinuxTestCase, on_path_in_extra_dirs
from world import LINUX, World, real_tmux_path

sys.path.insert(0, LINUX)
from ctmux_linux import terminal  # noqa: E402

KEYS_CONF = os.path.join(LINUX, "tmux.conf")
GHOSTTY_CONF = os.path.join(LINUX, "ghostty.conf")


def emulator(world, name):
    """A stub emulator on PATH that records its argv (one per line) in $W/<name>.args."""
    world.stub(name, 'for a in "$@"; do printf "%%s\\n" "$a"; done > "$W/%s.args"' % name)


def args_of(world, name):
    path = world.p(name + ".args")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read().splitlines()


class TerminalTest(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.cleanup)

    def test_bare_launch_runs_ctmux_in_ghostty_with_the_ctmux_config(self):
        emulator(self.w, "ghostty")
        res = self.w.run("terminal")
        self.assertEqual((res.rc, res.err), (0, ""))
        argv = args_of(self.w, "ghostty")
        self.assertEqual(argv[:3], ["--class=com.ctmux.Terminal", "--config-file=" + GHOSTTY_CONF, "-e"])
        self.assertEqual(argv[3], os.path.join(LINUX, "ctmux"))
        self.assertEqual(len(argv), 4)

    def test_a_bare_shell_from_xdg_terminal_exec_means_open_ctmux(self):
        emulator(self.w, "ghostty")
        for shell in (["/usr/bin/zsh"], ["-bash"], ["/bin/bash", "-l"]):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.w.p("ghostty.args"))
            self.assertEqual(self.w.run("terminal", "-e", *shell).rc, 0)
            self.assertEqual(args_of(self.w, "ghostty")[-1], os.path.join(LINUX, "ctmux"), shell)

    def test_a_real_command_runs_as_asked_not_as_ctmux(self):
        emulator(self.w, "ghostty")
        self.assertEqual(self.w.run("terminal", "-e", "htop", "-d", "5").rc, 0)
        self.assertEqual(args_of(self.w, "ghostty")[2:], ["-e", "htop", "-d", "5"])

    def test_title_and_working_directory_reach_the_emulator(self):
        emulator(self.w, "ghostty")
        self.assertEqual(self.w.run("terminal", "--title", "build", "--working-directory=" + self.w.home, "-e", "make").rc, 0)
        argv = args_of(self.w, "ghostty")
        self.assertIn("--title=build", argv)
        self.assertIn("--working-directory=" + self.w.home, argv)
        self.assertEqual(argv[-2:], ["-e", "make"])

    def test_open_in_terminal_for_a_directory_opens_a_window_there(self):
        emulator(self.w, "ghostty")
        self.assertEqual(self.w.run("terminal", "--working-directory=" + self.w.home, "-e", "/bin/bash").rc, 0)
        self.assertEqual(args_of(self.w, "ghostty")[-3:], [os.path.join(LINUX, "ctmux"), "here", self.w.home])

    def test_forcing_a_self_referencing_emulator_is_refused(self):
        emulator(self.w, "ghostty")
        for name in ("ctmux", "xdg-terminal-exec", "/usr/bin/xdg-terminal-exec"):
            res = self.w.run("terminal", extra={"CTMUX_TERMINAL": name})
            self.assertEqual(res.rc, 1, name)
            self.assertIn("would start ctmux terminal again", res.err)
        res = self.w.run("terminal", extra={"CTMUX_TERMINAL": "no-such-terminal"})
        self.assertEqual(res.rc, 1)
        self.assertIn("is not on PATH", res.err)
        self.assertIsNone(args_of(self.w, "ghostty"))

    def test_an_unusable_working_directory_fails_instead_of_opening_elsewhere(self):
        emulator(self.w, "ghostty")
        res = self.w.run("terminal", "--working-directory", self.w.p("nope"), "-e", "make")
        self.assertEqual(res.rc, 1)
        self.assertIn("cannot use working directory", res.err)
        self.assertIsNone(args_of(self.w, "ghostty"))

    def test_an_option_without_a_value_is_a_usage_error_not_a_command(self):
        emulator(self.w, "ghostty")
        res = self.w.run("terminal", "--title")
        self.assertEqual(res.rc, 2)
        self.assertIn("--title needs a value", res.err)
        self.assertIsNone(args_of(self.w, "ghostty"))

    def test_a_relative_working_directory_is_resolved_once(self):
        emulator(self.w, "ghostty")
        os.makedirs(os.path.join(self.w.home, "proj"))
        self.assertEqual(self.w.run("terminal", "--working-directory=proj", "-e", "make", cwd=self.w.home).rc, 0)
        wanted = os.path.realpath(os.path.join(self.w.home, "proj"))
        given = [os.path.realpath(a.split("=", 1)[1]) for a in args_of(self.w, "ghostty") if a.startswith("--working-directory=")]
        self.assertEqual(given, [wanted])

    def test_an_unknown_option_is_a_usage_error_not_a_command(self):
        emulator(self.w, "ghostty")
        res = self.w.run("terminal", "--hold", "-e", "foo")
        self.assertEqual(res.rc, 2)
        self.assertIn("unknown option --hold", res.err)
        self.assertIsNone(args_of(self.w, "ghostty"))

    def test_a_symlink_to_xdg_terminal_exec_is_not_an_emulator(self):
        emulator(self.w, "ptyxis")
        self.w.stub("xdg-terminal-exec", "exit 0")  # a real executable the symlink resolves to
        os.symlink(self.w.p("bin", "xdg-terminal-exec"), self.w.p("bin", "ghostty"))
        if on_path_in_extra_dirs("ghostty"):
            self.skipTest("a real ghostty is installed in a system dir")
        self.assertEqual(self.w.run("terminal", "-e", "htop").rc, 0)
        self.assertEqual(args_of(self.w, "ptyxis"), ["--new-window", "--", "htop"])

    def test_double_dash_separates_the_command(self):
        emulator(self.w, "ghostty")
        self.assertEqual(self.w.run("terminal", "--", "htop", "-d", "5").rc, 0)
        self.assertEqual(args_of(self.w, "ghostty")[2:], ["-e", "htop", "-d", "5"])

    def test_gnome_terminal_and_generic_emulator_command_lines(self):
        self.assertEqual(terminal.build_argv("gnome-terminal", ["htop"], "t", "/d"), ["gnome-terminal", "--title=t", "--working-directory=/d", "--", "htop"])
        self.assertEqual(terminal.build_argv("x-terminal-emulator", ["htop"], "t", "/d"), ["x-terminal-emulator", "-e", "htop"])

    def test_the_window_runs_this_checkouts_ctmux_even_when_another_is_on_path(self):
        emulator(self.w, "ghostty")
        other = self.w.p("other")
        os.makedirs(other)
        self.w.stub("ctmux", "exit 0")
        self.assertEqual(self.w.run("terminal").rc, 0)
        self.assertEqual(args_of(self.w, "ghostty")[-1], os.path.join(LINUX, "ctmux"))

    def test_falls_back_to_ptyxis_when_there_is_no_ghostty(self):
        if on_path_in_extra_dirs("ghostty"):
            self.skipTest("a real ghostty is installed in a system dir")
        emulator(self.w, "ptyxis")
        self.assertEqual(self.w.run("terminal", "-e", "htop").rc, 0)
        self.assertEqual(args_of(self.w, "ptyxis"), ["--new-window", "--", "htop"])

    def test_ctmux_terminal_env_override_picks_the_emulator(self):
        emulator(self.w, "ghostty")
        emulator(self.w, "kitty")
        self.assertEqual(self.w.run("terminal", "-e", "htop", extra={"CTMUX_TERMINAL": "kitty"}).rc, 0)
        self.assertEqual(args_of(self.w, "kitty"), ["-e", "htop"])
        self.assertIsNone(args_of(self.w, "ghostty"))

    def test_without_any_emulator_it_says_what_to_install(self):
        if any(on_path_in_extra_dirs(c) for c in ("ghostty", "ptyxis", "gnome-terminal", "x-terminal-emulator")):
            self.skipTest("a real terminal emulator is installed in a system dir")
        res = self.w.run("terminal")
        self.assertEqual(res.rc, 1)
        self.assertIn("no terminal emulator found", res.err)
        self.assertIn("sudo apt install ghostty", res.err)

    def test_ghostty_sends_the_sequences_tmux_binds(self):
        """The bytes of every ghostty.conf keybind are bound in tmux.conf (the KeyBindingTest below
        proves tmux acts on them)."""
        with open(GHOSTTY_CONF) as f:
            ghostty = f.read()
        with open(KEYS_CONF) as f:
            tmux = f.read()
        expect = {
            "ctrl+tab=csi:9;5u": "C-Tab",
            "ctrl+shift+tab=csi:9;6u": "C-BTab",
            "ctrl+shift+t=csi:116;6u": "C-S-t",
            "alt+one=text:\\x1b1": "M-1",
            "alt+nine=text:\\x1b9": "M-9",
        }
        for bind, tmux_key in expect.items():
            self.assertIn("keybind = " + bind, ghostty)
            self.assertIn("bind -n " + tmux_key + " ", tmux)


@unittest.skipUnless(shutil.which("tmux"), "no tmux")
class KeyBindingTest(unittest.TestCase):
    """Real tmux, real client on a pty, the exact bytes the Ghostty ctmux config sends."""

    def setUp(self):
        if os.environ.get("TMUX"):
            raise unittest.SkipTest("refusing to run tmux tests inside tmux (TMUX is set)")
        self.w = World()
        self.addCleanup(self.w.cleanup)
        self.env = dict(self.w.env(), TERM="xterm-256color")
        self.sock = "keys%d" % os.getpid()
        self.tm("-f", "/dev/null", "new-session", "-d", "-s", "s", "-x", "80", "-y", "24")
        self.addCleanup(self.stop)
        self.tm("source-file", KEYS_CONF)
        for _ in range(2):
            self.tm("new-window", "-t", "s:")
        self.tm("select-window", "-t", "s:0")
        self.attach("xterm-256color")

    def attach(self, term):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(real_tmux_path(), ["tmux", "-L", self.sock, "attach", "-t", "s"], dict(self.env, TERM=term))
        time.sleep(0.6)

    def restart_client(self, term):
        os.write(self.fd, b"\x02d")  # detach (prefix + d), then attach again as `term`
        self.reap()
        self.attach(term)

    def tm(self, *args):
        p = subprocess.run([real_tmux_path(), "-L", self.sock, *args], env=self.env, capture_output=True, text=True, timeout=20)
        return p.stdout.strip()

    def stop(self):
        # The same teardown PrivateTmux.kill uses: kill-server on this test's own socket (-L, private TMUX_TMPDIR),
        # so nothing else can be reached; exiting the shells one by one leaves servers behind when a pane is busy.
        self.tm("kill-server")
        self.reap()

    def reap(self):
        """Wait for the attached client to exit; keep draining the pty or it blocks writing its redraws."""
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                done, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                break
            if done:
                break
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    os.read(self.fd, 65536)
                except OSError:
                    pass
        os.close(self.fd)

    def press(self, data):
        os.write(self.fd, data)
        time.sleep(0.15)
        while select.select([self.fd], [], [], 0)[0]:
            os.read(self.fd, 65536)

    def window(self, want=None):
        """The current window index; when `want` is given, wait (up to 5 s) for it to arrive."""
        deadline = time.time() + 5
        while True:
            got = self.tm("display-message", "-p", "-t", "s", "#I")
            if want is None or got == want or time.time() > deadline:
                return got
            time.sleep(0.05)

    def test_ctrl_tab_cycles_windows_forward_and_wraps(self):
        seen = []
        for want in ("1", "2", "0", "1"):
            self.press(b"\x1b[9;5u")
            seen.append(self.window(want))
        self.assertEqual(seen, ["1", "2", "0", "1"])

    def test_ctrl_shift_tab_goes_back_and_wraps(self):
        self.press(b"\x1b[9;6u")
        self.assertEqual(self.window("2"), "2")
        self.press(b"\x1b[9;6u")
        self.assertEqual(self.window("1"), "1")

    def test_ctrl_tab_also_works_in_modify_other_keys_form(self):
        self.press(b"\x1b[27;5;9~")
        self.assertEqual(self.window("1"), "1")

    def test_ctrl_shift_t_opens_a_window(self):
        before = int(self.tm("display-message", "-p", "-t", "s", "#{session_windows}"))
        self.press(b"\x1b[116;6u")
        deadline = time.time() + 5
        while int(self.tm("display-message", "-p", "-t", "s", "#{session_windows}")) == before and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(int(self.tm("display-message", "-p", "-t", "s", "#{session_windows}")), before + 1)

    def test_alt_digit_passes_through_to_programs_in_non_ghostty_clients(self):
        """This client is xterm-256color, not Ghostty: Alt+2 must reach the program in the pane."""
        self.tm("send-keys", "-t", "s:0", "cat -v", "Enter")
        self.press(b"\x1b2")
        self.press(b"\r")
        deadline = time.time() + 5
        shown = ""
        while "^[2" not in shown and time.time() < deadline:
            shown = self.tm("capture-pane", "-p", "-t", "s:0")
            time.sleep(0.05)
        self.assertIn("^[2", shown)
        self.assertEqual(self.window(), "0")

    @unittest.skipUnless(subprocess.run(["infocmp", "xterm-ghostty"], capture_output=True, check=False).returncode == 0, "no xterm-ghostty terminfo")
    def test_alt_digit_selects_that_window_in_a_ghostty_client(self):
        self.restart_client("xterm-ghostty")
        self.press(b"\x1b2")
        self.assertEqual(self.window("2"), "2")
        self.press(b"\x1b1")
        self.assertEqual(self.window("1"), "1")


class CtmuxLoadsKeysTest(LinuxTestCase):
    def test_ensure_loads_the_key_bindings_into_the_new_server(self):
        self.assertEqual(self.w.run("ensure").rc, 0)
        self.assertTrue(any(c.startswith("source-file ") and c.endswith("tmux.conf") for c in self.tmux_calls()), self.tmux_calls())
        keys = self.tmux.cmd("list-keys", "-T", "root").stdout
        self.assertIn("C-Tab", keys)
        self.assertIn("next-window", keys)

    def test_a_syntax_error_in_the_users_conf_is_reported_not_swallowed(self):
        os.makedirs(os.path.join(self.w.home, ".config", "ctmux"))
        with open(os.path.join(self.w.home, ".config", "ctmux", "tmux.conf"), "w") as f:
            f.write("bind -n C-Tab not-a-tmux-command\n")
        res = self.w.run("ensure")
        self.assertEqual(res.rc, 0)
        self.assertIn("not-a-tmux-command", res.err + res.out)

    def test_attaching_repeatedly_does_not_grow_the_server_state(self):
        self.assertEqual(self.w.run_tty().rc, 0)
        before = self.tmux.cmd("show", "-s").stdout
        for _ in range(3):
            self.assertEqual(self.w.run_tty().rc, 0)  # every attach sources the key bindings again
        self.assertEqual(self.tmux.cmd("show", "-s").stdout, before)

    def test_here_opens_a_window_in_the_directory_of_the_canonical_session(self):
        d = os.path.join(self.w.home, "project")
        os.makedirs(d)
        res = self.w.run_tty("here", d)
        self.assertEqual(res.rc, 0, res.err)
        paths = self.tmux.cmd("list-windows", "-t", "=main", "-F", "#{pane_current_path}").stdout.split()
        self.assertEqual(sorted(os.path.realpath(p) for p in paths), sorted(os.path.realpath(p) for p in [self.w.home, d]))

    def test_here_without_a_terminal_creates_no_window(self):
        res = self.w.run("here", self.w.home)
        self.assertEqual(res.rc, 1)
        self.assertIn("attaching needs a terminal", res.err)
        self.assertEqual(self.tmux.sessions(), [])

    def test_here_with_a_missing_directory_still_attaches(self):
        res = self.w.run_tty("here", os.path.join(self.w.home, "gone"))
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(len(self.tmux.cmd("list-windows", "-t", "=main").stdout.splitlines()), 1)

    def test_a_users_own_conf_is_sourced_after_ours(self):
        os.makedirs(os.path.join(self.w.home, ".config", "ctmux"))
        mine = os.path.join(self.w.home, ".config", "ctmux", "tmux.conf")
        with open(mine, "w") as f:
            f.write("bind -n C-Tab previous-window\n")
        self.assertEqual(self.w.run("ensure").rc, 0)
        sourced = [c.split()[-1] for c in self.tmux_calls() if c.startswith("source-file")]
        self.assertEqual(sourced, [os.path.join(LINUX, "tmux.conf"), mine])
        self.assertIn("previous-window", self.tmux.cmd("list-keys", "-T", "root").stdout.split("C-Tab")[1].splitlines()[0])


if __name__ == "__main__":
    unittest.main()
