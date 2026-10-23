"""ctmux for Linux — tmux sessions as workspaces, over plain tmux.

On macOS ctmux is the shell script bin/ctmux (a wrapper around cmux's Remote
tmux); cmux has no Linux build, so on Linux this package provides the same
commands over plain tmux — no cmux, ssh or launchd.

Usage:
  ctmux            ensure the canonical session exists, then attach to it
                   (switch-client when already inside tmux)
  ctmux ls         fzf picker over session:window pairs; Enter attaches to that
                   session AND selects that window; pick "+ new session" to
                   create one
  ctmux new [name] create a new tmux session (auto-named ws-HHMMSS if omitted)
                   or open it if it exists
  ctmux ensure     create the canonical session detached if it is missing;
                   idempotent, never needs a terminal
  ctmux here [dir] open a new window in dir in the canonical session and attach
  ctmux terminal [-e CMD ...]
                   open ctmux (or CMD) in a terminal window: the desktop's terminal
                   entry, see terminal.py

The canonical session is tmux.anti_sprawl.session from rig's config, else main.
"""
import sys

from .util import have, say

UNKNOWN = "ctmux: unknown command '%s' (try: ls, new [name], here [dir], ensure, terminal, or no args)"


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    cmd = argv[0] if argv else ""
    if cmd not in ("", "ls", "new", "ensure", "terminal", "here"):
        say(UNKNOWN % cmd)
        return 1
    if cmd == "terminal":
        from .terminal import main as terminal

        return terminal(argv[1:])
    from .tmux_backend import Tmux, pkg_hint

    if not have("tmux"):
        say(f"ctmux: tmux not found — fix: {pkg_hint('tmux')}")
        return 1
    t = Tmux()
    if cmd == "ls":
        t.picker()
    elif cmd == "new":
        t.new_session(argv[1] if len(argv) > 1 else "")
    elif cmd == "ensure":
        t.ensure()
    elif cmd == "here":
        t.here(argv[1] if len(argv) > 1 else "")
    else:
        t.bare()
    return 0
