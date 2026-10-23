"""Fake external commands for the Linux ctmux tests.

Every stub in a test world's bin dir is a two-line sh wrapper that runs
`python3 stubs.py NAME ARGS...`. The world directory is $W; behavior is
configured by $W/cfg.json (written by the test). Every call appends one JSON
line to $W/trace: {"cmd": NAME, "argv": [...], ...}.

`tmux` runs in one of two modes: with cfg "real_tmux" set it is a wrapper
around the REAL tmux pinned to a private socket (see real_tmux()); otherwise
it is a canned fake. `fzf` picks the first row containing cfg "fzf_pick" (and
cancels without one), recording the rows it was offered.
"""
import json
import os
import subprocess
import sys

W = os.environ["W"]


def path(*parts):
    return os.path.join(W, *parts)


def load_cfg():
    try:
        with open(path("cfg.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


CFG = load_cfg()


def trace(entry):
    line = json.dumps(entry) + "\n"
    fd = os.open(path("trace"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def real_tmux(argv, rt):
    """Wrapper mode: the real tmux on a private socket (-L, no config file,
    per-world TMUX_TMPDIR). attach-session / switch-client need a client
    terminal, so they are only logged; a chained `select-window ... ; attach-session`
    still runs its select-window half for real."""
    if not os.path.isdir(rt["tmpdir"]) or not rt["sock"]:
        sys.exit("stubs: refusing to touch tmux without an existing private TMUX_TMPDIR and a -L socket name")
    real = [rt["real"], "-L", rt["sock"], "-f", "/dev/null"]
    env = dict(os.environ, TMUX_TMPDIR=rt["tmpdir"])
    env.pop("TMUX", None)
    if "attach-session" in argv or "switch-client" in argv:
        if ";" in argv:
            subprocess.run(real + argv[: argv.index(";")], env=env)
        return 0
    os.execve(real[0], real + argv, env)


def tmux(argv):
    trace({"cmd": "tmux", "argv": argv})
    if CFG.get("real_tmux"):
        return real_tmux(argv, CFG["real_tmux"])
    return 0


def fzf(argv):
    rows = sys.stdin.read()
    trace({"cmd": "fzf", "argv": argv, "stdin": rows.split("\n")[:-1] if rows else []})
    pick = CFG.get("fzf_pick")
    if pick is not None:
        for row in rows.split("\n"):
            if pick in row:
                print(row)
                return 0
    return 1


def main():
    name, argv = sys.argv[1], sys.argv[2:]
    if name == "tmux":
        return tmux(argv)
    if name == "fzf":
        return fzf(argv)
    trace({"cmd": name, "argv": argv})
    return 0


if __name__ == "__main__":
    sys.exit(main())
