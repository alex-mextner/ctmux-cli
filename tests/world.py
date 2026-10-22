"""A fake world for black-box tests of the Linux ctmux: stub external commands
on PATH, a fixture HOME, and a trace of every external command a run issued.
"""
import json
import os
import pty
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
LINUX = os.path.join(ROOT, "linux")
LAUNCHER = os.path.join(LINUX, "ctmux")
STUBS = os.path.join(TESTS, "stubs.py")

# Commands ctmux may call; each is a logging stub.
STUB_NAMES = ("tmux", "fzf")
# ctmux resolves tmux on the caller's PATH only (no PATH changes of its own).
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def read_file(path):
    with open(path) as f:
        return f.read()


class Result:
    def __init__(self, rc, out, err):
        self.rc, self.out, self.err = rc, out, err

    def __repr__(self):
        return "Result(rc=%r, out=%r, err=%r)" % (self.rc, self.out, self.err)


class World:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="ctmux-test.")
        self.bin = os.path.join(self.root, "bin")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.bin)
        os.makedirs(self.home)
        # Belt and braces: whatever tmux a run reaches, TMUX_TMPDIR points at this
        # world's own (existing) dir, never at the real /tmp/tmux-UID.
        self.tmuxdir = os.path.join(self.root, "t")
        os.makedirs(self.tmuxdir)
        self.cfg = {}
        self.write_cfg_json()
        open(self.p("trace"), "w").close()
        for name in STUB_NAMES:
            self.stub(name)
        # The launcher runs `python3`; the interpreter running the tests will do.
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))
        self.path = self.bin + ":" + SYSTEM_PATH

    # ---- plumbing ----

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def stub(self, name, body=None):
        """A logging stub backed by stubs.py, or a custom sh body."""
        target = os.path.join(self.bin, name)
        if body is None:
            body = 'exec "%s" "%s" %s "$@"' % (sys.executable, STUBS, name)
        with open(target, "w") as f:
            f.write("#!/bin/sh\n%s\n" % body)
        os.chmod(target, 0o755)

    def remove_stub(self, name):
        os.unlink(os.path.join(self.bin, name))

    def configure(self, **kw):
        self.cfg.update(kw)
        self.write_cfg_json()

    def write_cfg_json(self):
        with open(self.p("cfg.json"), "w") as f:
            json.dump(self.cfg, f)

    def env(self, extra=None, in_tmux=None):
        env = {"HOME": self.home, "PATH": self.path, "W": self.root, "LC_ALL": "C.UTF-8", "TMUX_TMPDIR": self.tmuxdir}
        if in_tmux is not None:
            env["TMUX"] = in_tmux
        if extra:
            env.update(extra)
        return env

    # ---- running ----

    def run(self, *args, stdin=None, timeout=120, **envkw):
        p = subprocess.run(
            [LAUNCHER] + list(args),
            env=self.env(**envkw),
            input=stdin if stdin is not None else "",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return Result(p.returncode, p.stdout, p.stderr)

    def run_tty(self, *args, typed="", timeout=60, **envkw):
        """Run with stdin+stdout on a pty (attach/switch insist on a terminal);
        stderr goes to a file. Returns Result(rc, pty output, stderr)."""
        err_path = self.p("tty_err")
        pid, fd = pty.fork()
        if pid == 0:
            os.dup2(os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), 2)
            os.execve(LAUNCHER, [LAUNCHER] + list(args), self.env(**envkw))
        if typed:
            os.write(fd, typed.encode())
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        _, status = os.waitpid(pid, 0)
        rc = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
        return Result(rc, buf.decode(errors="replace"), read_file(err_path))

    # ---- observing ----

    def trace(self):
        with open(self.p("trace")) as f:
            return [json.loads(line) for line in f if line.strip()]

    def called(self, cmd):
        return [t for t in self.trace() if t["cmd"] == cmd]


# ---- real tmux on a private socket ----

def real_tmux_path():
    return shutil.which("tmux")


class PrivateTmux:
    """The real tmux, pinned to a private socket (-L) with no config file under
    a per-world TMUX_TMPDIR. The world's `tmux` stub switches to wrapper mode
    (see stubs.real_tmux): every call is logged, attach-session / switch-client
    are faked (they need a client terminal). Nothing here can reach a live tmux
    server: the private dir must exist (tmux silently falls back to the real
    /tmp/tmux-UID otherwise) and a -L name is always given."""

    def __init__(self, world):
        if os.environ.get("TMUX"):
            raise unittest.SkipTest("refusing to run tmux-backend tests inside tmux (TMUX is set)")
        self.world = world
        self.real = real_tmux_path()
        # Short names: a unix socket path is limited to 104 bytes on macOS and
        # its $TMPDIR is already ~50 of them.
        self.tmpdir = world.tmuxdir
        self.sock = "ct" + world.root.rsplit(".", 1)[-1]
        world.configure(real_tmux={"real": self.real, "tmpdir": self.tmpdir, "sock": self.sock})

    def cmd(self, *args):
        assert os.path.isdir(self.tmpdir) and self.sock, "refusing to run tmux without a private TMUX_TMPDIR and -L socket"
        env = dict(os.environ, TMUX_TMPDIR=self.tmpdir)
        env.pop("TMUX", None)
        return subprocess.run([self.real, "-L", self.sock, "-f", "/dev/null"] + list(args), env=env, capture_output=True, text=True)

    def sessions(self):
        return sorted(self.cmd("list-sessions", "-F", "#{session_name}").stdout.split())

    def kill(self):
        if os.path.isdir(self.tmpdir):
            self.cmd("kill-server")
