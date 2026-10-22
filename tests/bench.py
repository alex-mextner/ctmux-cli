"""Wall-clock latency of the Linux ctmux (linux/ctmux) on a private tmux server holding
N_SESSIONS sessions (default 50). Not part of the test suite (timings are
machine-dependent); run it by hand when touching a hot path:

    python3 tests/bench.py                      # N_SESSIONS=50 RUNS=30
    N_SESSIONS=200 RUNS=50 python3 tests/bench.py

Everything is isolated: HOME and TMUX_TMPDIR point into a temp dir (so the
server socket and any ~/.tmux.conf are private and a real tmux server is never
reachable), and stdin/stdout are not terminals, so ctmux stops right before it
would exec `tmux attach` — the timings cover everything ctmux does itself. The
fzf used by `ls` is an sh stub that picks the first row (its own ~1 ms startup
is included in the `ls` row).
"""
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTMUX = os.path.join(ROOT, "linux", "ctmux")
N_SESSIONS = int(os.environ.get("N_SESSIONS", "50"))
RUNS = int(os.environ.get("RUNS", "30"))
REAL_TMUX = shutil.which("tmux")


def measure(label, argv, env, runs=RUNS, before=None):
    times = []
    for _ in range(runs):
        if before:
            before()
        t0 = time.perf_counter()
        subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    p95 = times[min(len(times) - 1, int(len(times) * 0.95 + 0.5))]
    print("%-42s min %6.1f  median %6.1f  p95 %6.1f  max %6.1f" % (label, times[0], statistics.median(times), p95, times[-1]))


def main():
    if not REAL_TMUX:
        sys.exit("bench: tmux not found")
    if os.environ.get("TMUX"):
        sys.exit("bench: refusing to run inside tmux (TMUX is set)")
    tmp = tempfile.mkdtemp(prefix="ctmux-bench.")
    os.makedirs(os.path.join(tmp, "home", ".config", "rig"))
    os.makedirs(os.path.join(tmp, "t"))
    os.makedirs(os.path.join(tmp, "bin"))
    fzf = os.path.join(tmp, "bin", "fzf")
    with open(fzf, "w") as f:
        f.write('#!/bin/sh\nIFS= read -r first\nprintf "%s\\n" "$first"\n')
    os.chmod(fzf, 0o755)
    env = {"HOME": os.path.join(tmp, "home"), "TMUX_TMPDIR": os.path.join(tmp, "t"), "PATH": os.path.join(tmp, "bin") + ":" + os.environ["PATH"], "LC_ALL": "C.UTF-8"}
    # ctmux runs plain `tmux`, so a wrapper first on PATH pins every call to a
    # private socket (-L, no config file) under the private TMUX_TMPDIR, and
    # refuses to run if that dir is missing (tmux would silently fall back to
    # the real /tmp/tmux-UID). The wrapper's exec adds ~1 ms to each tmux call.
    wrapper = os.path.join(tmp, "bin", "tmux")
    with open(wrapper, "w") as f:
        f.write('#!/bin/sh\n[ -d "$TMUX_TMPDIR" ] || { echo "bench: private TMUX_TMPDIR missing" >&2; exit 2; }\nexec "%s" -L "ctmux-bench-%d" -f /dev/null "$@"\n' % (REAL_TMUX, os.getpid()))
    os.chmod(wrapper, 0o755)

    def tmux(*a):
        return subprocess.run([wrapper] + list(a), env=env, capture_output=True, text=True)

    try:
        # A rig config of realistic size with the key near the end, so the
        # reader pays for a full scan.
        lines = ["tools:"] + ["  item%d: { enabled: true }" % i for i in range(150)] + ["tmux:", "  resurrect:"]
        lines += ["    opt%d: %d" % (i, i) for i in range(100)] + ["  anti_sprawl:", "    enabled: true", "    session: main"]
        with open(os.path.join(tmp, "home", ".config", "rig", "config.yaml"), "w") as f:
            f.write("\n".join(lines) + "\n")
        tmux("new-session", "-d", "-s", "main")
        for i in range(1, N_SESSIONS + 1):
            tmux("new-session", "-d", "-s", "bench%d" % i)
            tmux("new-window", "-d", "-t", "=bench%d" % i)
        print("bench: %d sessions, %d windows, %d runs per row; times in ms; python %s" % (
            len(tmux("list-sessions").stdout.splitlines()), len(tmux("list-windows", "-a").stdout.splitlines()), RUNS, sys.version.split()[0]))
        py = sys.executable
        measure("fork+exec baseline (true)", ["/bin/true"], env)
        measure("python3 -c pass", [py, "-c", "pass"], env)
        measure("python3 -S -E -s -c pass", [py, "-S", "-E", "-s", "-c", "pass"], env)
        measure("tmux has-session via wrapper (floor)", [wrapper, "has-session", "-t", "=main"], env)
        measure("ctmux ensure (session exists)", [CTMUX, "ensure"], env)
        measure("ctmux ls (list + fzf stub + pick)", [CTMUX, "ls"], env)
        measure("ctmux new <existing>", [CTMUX, "new", "bench1"], env)
        counter = [0]

        def fresh():
            counter[0] += 1
            return [CTMUX, "new", "fresh-%d-%d" % (os.getpid(), counter[0])]

        times = []
        for _ in range(RUNS):
            argv = fresh()
            t0 = time.perf_counter()
            subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        print("%-42s min %6.1f  median %6.1f  p95 %6.1f  max %6.1f" % ("ctmux new <fresh name>", times[0], statistics.median(times), times[int(len(times) * 0.95)], times[-1]))
        os.unlink(os.path.join(tmp, "home", ".config", "rig", "config.yaml"))
        measure("ctmux ensure (no rig config)", [CTMUX, "ensure"], env)
        measure("ctmux ensure (cold: no tmux server)", [CTMUX, "ensure"], env, runs=10, before=lambda: tmux("kill-server"))
    finally:
        tmux("kill-server")  # the wrapper pins this to the bench's own -L socket
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
