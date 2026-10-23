"""Process, PATH and output helpers.

Deliberately tiny and stdlib-only at import time: the tmux backend's hot path
(`ctmux ensure` is one tmux call) must not pay for `subprocess`, `shutil`,
`json` or friends. External commands are spawned with os.posix_spawn and at most
one pipe, which covers every shape ctmux needs (capture stdout, optionally
merging stderr into it; feed stdin; inherit everything).
"""
import os
import sys

LINUX_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <checkout>/linux

def which(cmd):
    """First executable `cmd` on PATH (like `command -v`), or None."""
    if "/" in cmd:
        return cmd if os.access(cmd, os.X_OK) else None
    for d in os.environ.get("PATH", "").split(":"):
        full = os.path.join(d or ".", cmd)
        if os.access(full, os.X_OK) and not os.path.isdir(full):
            return full
    return None


have = which


def say(msg):
    """One line on stderr, unbuffered (it may interleave with child output)."""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def out(msg, end="\n"):
    sys.stdout.write(msg + end)
    sys.stdout.flush()


class Stdio:
    """Where a child's stdout / stderr go."""

    INHERIT, DEVNULL, PIPE, MERGE = "inherit", "devnull", "pipe", "merge"


def run(argv, *, stdin=None, stdout=Stdio.INHERIT, stderr=Stdio.INHERIT, env=None):
    """Run argv; return (returncode, captured stdout text).

    stdout=PIPE captures it; stderr=MERGE sends stderr into the same pipe
    (`2>&1`); DEVNULL discards. `stdin` is text to feed (None = inherit).
    A command that is not found returns 127 like a shell would.
    """
    exe = which(argv[0])
    if exe is None:
        return 127, ""
    actions = []
    opened = []  # fds to close in the parent after spawning
    rpipe = wpipe = in_r = in_w = None
    if stdout == Stdio.PIPE or stderr == Stdio.MERGE:
        rpipe, wpipe = os.pipe()
        opened += [rpipe, wpipe]
        if stdout == Stdio.PIPE:
            actions.append((os.POSIX_SPAWN_DUP2, wpipe, 1))
        elif stdout == Stdio.DEVNULL:
            actions.append((os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0))
    elif stdout == Stdio.DEVNULL:
        actions.append((os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0))
    if stderr == Stdio.MERGE:
        actions.append((os.POSIX_SPAWN_DUP2, wpipe, 2))
    elif stderr == Stdio.DEVNULL:
        actions.append((os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0))
    if stdin is not None:
        in_r, in_w = os.pipe()
        opened += [in_r, in_w]
        actions.append((os.POSIX_SPAWN_DUP2, in_r, 0))
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        pid = os.posix_spawn(exe, argv, dict(env) if env is not None else os.environ, file_actions=actions)
    except OSError:
        for fd in opened:
            os.close(fd)
        return 127, ""
    if wpipe is not None:
        os.close(wpipe)
    if in_r is not None:
        os.close(in_r)
    chunks = []
    data = stdin.encode() if in_w is not None else b""
    if in_w is not None and rpipe is None:
        try:
            os.write(in_w, data)
        except BrokenPipeError:
            pass
        os.close(in_w)
        in_w = None
    elif in_w is not None:
        # Feed stdin and drain stdout together: a child that echoes its input
        # (cat) would otherwise block on a full output pipe while we block on a
        # full input pipe.
        import select

        os.set_blocking(in_w, False)
        off = 0
        while in_w is not None or rpipe is not None:
            rl, wl, _ = select.select([rpipe] if rpipe is not None else [], [in_w] if in_w is not None else [], [])
            if wl:
                try:
                    off += os.write(in_w, data[off : off + 65536])
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    off = len(data)
                if off >= len(data):
                    os.close(in_w)
                    in_w = None
            if rl:
                chunk = os.read(rpipe, 65536)
                if chunk:
                    chunks.append(chunk)
                else:
                    os.close(rpipe)
                    rpipe = None
    if rpipe is not None:
        while True:
            chunk = os.read(rpipe, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        os.close(rpipe)
    _, status = os.waitpid(pid, 0)
    rc = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
    return rc, b"".join(chunks).decode("utf-8", "replace")


def sh_out(text):
    """What `$(...)` does to captured text: strip trailing newlines."""
    return text.rstrip("\n")


def exec_replace(argv):
    """exec argv in place (no process left behind; its exit status is ours)."""
    sys.stdout.flush()
    sys.stderr.flush()
    exe = which(argv[0])
    if exe is None:
        say("ctmux: %s: command not found" % argv[0])
        sys.exit(127)
    os.execv(exe, argv)
