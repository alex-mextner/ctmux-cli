"""The Linux ctmux (linux/ctmux, package ctmux_linux), black-box.

These run the REAL tmux, but only through the world's `tmux` wrapper stub: a
private socket (-L, no config file, per-world TMUX_TMPDIR) that logs every call
and fakes attach-session / switch-client (they need a client terminal).
"""
import os
import sys
import unittest

from world import LINUX, PrivateTmux, World, real_tmux_path

EXTRA_DIRS = ("/usr/local/bin", "/home/linuxbrew/.linuxbrew/bin")


def on_path_in_extra_dirs(cmd):
    """ctmux prepends /usr/local/bin and Linuxbrew's bin to PATH, so a hermetic
    PATH can't hide a command installed there; such tests skip instead of lying."""
    return any(os.access(os.path.join(d, cmd), os.X_OK) for d in EXTRA_DIRS)


@unittest.skipUnless(real_tmux_path(), "no tmux")
class LinuxTestCase(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.addCleanup(self.w.cleanup)
        self.tmux = PrivateTmux(self.w)
        self.addCleanup(self.tmux.kill)

    def tmux_calls(self):
        return [" ".join(t["argv"]) for t in self.w.called("tmux")]


class EnsureTest(LinuxTestCase):
    def test_creates_the_canonical_session_without_mac_tools(self):
        res = self.w.run("ensure")
        self.assertEqual((res.rc, res.out, res.err), (0, "", ""))
        self.assertEqual(self.tmux.sessions(), ["main"])
        used = {t["cmd"] for t in self.w.trace()}
        self.assertEqual(used, {"tmux"}, "macOS-only tool (or anything else) called: %s" % used)

    def test_is_idempotent(self):
        self.w.run("ensure")
        res = self.w.run("ensure")
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(self.tmux.sessions(), ["main"])
        self.assertEqual(sum(c.startswith("new-session") for c in self.tmux_calls()), 1, "session created more than once")

    def test_leaves_an_existing_session_alone(self):
        self.tmux.cmd("new-session", "-d", "-s", "main")
        self.tmux.cmd("new-window", "-d", "-t", "=main")
        res = self.w.run("ensure")
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(len(self.tmux.cmd("list-windows", "-t", "=main").stdout.splitlines()), 2)

    def rig_config(self, text):
        os.makedirs(os.path.join(self.w.home, ".config", "rig"))
        with open(os.path.join(self.w.home, ".config", "rig", "config.yaml"), "w") as f:
            f.write(text)

    def test_canonical_session_comes_from_rig_config(self):
        self.rig_config(
            "# rig config\ntools:\n  session: wrong-tools\ntmux:\n  resurrect:\n    session: wrong-resurrect\n"
            '  anti_sprawl:\n    enabled: true\n    session: "work.1"   # canonical, and tmux would rename the dot\n'
            "  boot:\n    session: wrong-boot\n"
        )
        self.assertEqual(self.w.run("ensure").rc, 0)
        self.assertEqual(self.tmux.sessions(), ["work_1"])

    def test_canonical_session_defaults_to_main_without_the_key(self):
        self.rig_config("tmux:\n  boot:\n    session: not-this\nother:\n  anti_sprawl:\n    session: not-this-either\n")
        self.assertEqual(self.w.run("ensure").rc, 0)
        self.assertEqual(self.tmux.sessions(), ["main"])


class NewTest(LinuxTestCase):
    def test_creates_and_attaches(self):
        res = self.w.run_tty("new", "proj")
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(self.tmux.sessions(), ["proj"])
        self.assertIn("attach-session -t =proj", self.tmux_calls())

    def test_existing_session_attaches_without_recreating(self):
        self.tmux.cmd("new-session", "-d", "-s", "proj")
        res = self.w.run_tty("new", "proj")
        self.assertEqual(res.rc, 0, res.err)
        self.assertIn("session 'proj' already exists, attaching to it.", res.err)
        self.assertEqual(self.tmux.sessions(), ["proj"])
        self.assertIn("attach-session -t =proj", self.tmux_calls())

    def test_unnamed_uses_a_timestamp_name(self):
        self.assertEqual(self.w.run_tty("new").rc, 0)
        (name,) = self.tmux.sessions()
        self.assertRegex(name, r"^ws-\d{6}$")

    def test_names_are_sanitized_the_way_tmux_does(self):
        res = self.w.run_tty("new", "a.b:c")
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(self.tmux.sessions(), ["a_b_c"])
        self.assertIn("attach-session -t =a_b_c", self.tmux_calls())

    def test_inside_tmux_switches_the_client(self):
        res = self.w.run("new", "proj", in_tmux="/fake/socket,1,0")
        self.assertEqual(res.rc, 0, res.err)
        self.assertIn("switch-client -t =proj", self.tmux_calls())
        self.assertFalse([c for c in self.tmux_calls() if "attach-session" in c], "attached instead of switching")

    def test_without_a_terminal_says_so_and_names_the_fix(self):
        res = self.w.run("new", "proj")
        self.assertEqual(res.rc, 1)
        self.assertIn("needs a terminal", res.err)
        self.assertIn("ctmux ensure", res.err)

    def test_bare_ctmux_ensures_and_attaches_to_the_canonical_session(self):
        res = self.w.run_tty()
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(self.tmux.sessions(), ["main"])
        self.assertIn("attach-session -t =main", self.tmux_calls())


class PickerTest(LinuxTestCase):
    def two_sessions(self):
        self.tmux.cmd("new-session", "-d", "-s", "alpha")
        self.tmux.cmd("new-window", "-d", "-t", "=alpha")
        self.tmux.cmd("new-session", "-d", "-s", "beta")

    def test_attaches_to_the_picked_window(self):
        self.two_sessions()
        self.w.configure(fzf_pick="alpha\t1 (")
        res = self.w.run_tty("ls")
        self.assertEqual(res.rc, 0, res.err)
        self.assertIn("select-window -t =alpha:1 ; attach-session -t =alpha", self.tmux_calls())
        active = self.tmux.cmd("list-windows", "-t", "=alpha", "-F", "#{window_index}:#{window_active}").stdout.split()
        self.assertIn("1:1", active, "window 1 not active")

    def test_session_name_with_a_tab_is_escaped_by_tmux_so_rows_stay_parseable(self):
        self.tmux.cmd("new-session", "-d", "-s", "team\twork")
        self.w.configure(fzf_pick="team")
        res = self.w.run_tty("ls")
        self.assertEqual(res.rc, 0, res.err)
        (row,) = [r for r in self.w.called("fzf")[0]["stdin"] if r.startswith("team")]
        self.assertEqual(row.count("\t"), 1, "tmux no longer escapes tabs in session names: %r" % row)
        self.assertTrue(any("attach-session -t =team" in c for c in [" ".join(t["argv"]) for t in self.w.called("tmux")]))

    def test_lists_every_window_and_a_new_session_row(self):
        self.two_sessions()
        res = self.w.run_tty("ls")  # no fzf_pick: the fzf stub cancels
        self.assertEqual(res.rc, 0, res.err)
        (fzf,) = self.w.called("fzf")
        rows = fzf["stdin"]
        self.assertEqual(len([r for r in rows if r.startswith("alpha\t")]), 2, rows)
        self.assertTrue(any(r.startswith("beta\t0 (") for r in rows), rows)
        self.assertEqual(rows[-1], "+ new session")
        self.assertFalse([c for c in self.tmux_calls() if "attach-session" in c], "attached after a cancelled picker")

    def test_new_session_row_prompts_for_a_name(self):
        self.w.configure(fzf_pick="+ new session")
        res = self.w.run_tty("ls", typed="fresh\n")
        self.assertEqual(res.rc, 0, res.err)
        self.assertEqual(self.tmux.sessions(), ["fresh"])
        self.assertIn("attach-session -t =fresh", self.tmux_calls())

    @unittest.skipIf(on_path_in_extra_dirs("fzf"), "fzf installed in an extra dir")
    def test_without_fzf_names_the_fix(self):
        self.w.remove_stub("fzf")
        self.w.path = self.w.bin
        res = self.w.run("ls")
        self.assertEqual(res.rc, 1)
        self.assertIn("fzf not found", res.err)
        self.assertIn("brew install fzf", res.err, "no brew hint without apt")
        self.w.stub("apt-get", "exit 0")
        res = self.w.run("ls")
        self.assertIn("sudo apt install fzf", res.err, "no apt hint with apt-get")


class MissingToolTest(unittest.TestCase):
    @unittest.skipIf(on_path_in_extra_dirs("tmux"), "tmux installed in an extra dir")
    def test_without_tmux_names_the_fix(self):
        w = World()
        self.addCleanup(w.cleanup)
        w.remove_stub("tmux")
        w.path = w.bin
        res = w.run("ensure")
        self.assertEqual(res.rc, 1)
        self.assertIn("tmux not found", res.err)
        self.assertIn("install tmux", res.err)

    def test_unknown_command_is_rejected(self):
        w = World()
        self.addCleanup(w.cleanup)
        res = w.run("frobnicate")
        self.assertEqual(res.rc, 1)
        self.assertEqual(res.err, "ctmux: unknown command 'frobnicate' (try: ls, new [name], ensure, or no args)\n")


class RigConfigTest(unittest.TestCase):
    def read(self, text):
        import tempfile

        sys.path.insert(0, LINUX)
        from ctmux_linux import rigcfg

        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            f.write(text)
            f.flush()
            return rigcfg.canonical_session(f.name)

    def test_values(self):
        block = "tmux:\n  anti_sprawl:\n    session: %s\n"
        self.assertEqual(self.read(block % "work"), "work")
        self.assertEqual(self.read(block % "'single'"), "single")
        self.assertEqual(self.read(block % '"a#b"'), "a#b")
        self.assertEqual(self.read(block % "plain   # comment"), "plain")
        self.assertEqual(self.read(block % ""), "main")

    def test_falls_back_to_main(self):
        self.assertEqual(self.read(""), "main")
        self.assertEqual(self.read("tmux: {anti_sprawl: {session: flow}}\n"), "main", "flow style is documented as unsupported")
        self.assertEqual(self.read("tmux:\n  anti_sprawl:\n    enabled: true\n"), "main")
        self.assertEqual(self.read("not: tmux\ntmux_other:\n  anti_sprawl:\n    session: x\n"), "main")

    def test_missing_file(self):
        sys.path.insert(0, LINUX)
        from ctmux_linux import rigcfg

        self.assertEqual(rigcfg.canonical_session("/nonexistent/config.yaml"), "main")


if __name__ == "__main__":
    unittest.main()
