"""The tmux backend: the same commands over plain tmux — no cmux, no ssh, no
launchd. For Linux (and macOS without cmux).

Why plain tmux on Linux: cmux is a macOS-only app (no Linux build) and its
Remote tmux feature exists only to render tmux windows/panes as native
tabs/splits. A Linux terminal already renders tmux itself, so the useful part
left is the session-level workflow (pick / create / ensure), which needs
nothing but tmux. Not looping through ssh to 127.0.0.1 also removes sshd, host
keys and key-based login as prerequisites. Hot paths are one or two tmux calls;
no subprocess/json/re import happens here.
"""
import os
import sys

from . import rigcfg, util
from .util import Stdio, have, run, say, sh_out

KEYS_CONF = os.path.join(util.LINUX_DIR, "tmux.conf")
LIST_FORMAT = "#{session_name}\t#{window_index} (#{window_name})"


def sanitize(name):
    """tmux turns "." and ":" in session names into "_"; do the same up front,
    otherwise a name tmux would rewrite is never found again by `has-session =name`."""
    return name.replace(".", "_").replace(":", "_")


def pkg_hint(pkg):
    """How to install `pkg` with whichever package manager this machine has."""
    return f"sudo apt install {pkg}" if have("apt-get") else f"brew install {pkg}"


class Tmux:
    def __init__(self):
        self.home = os.environ["HOME"]
        self.rig_cfg = os.path.join(self.home, ".config", "rig", "config.yaml")

    def canonical_session(self):
        return sanitize(rigcfg.canonical_session(self.rig_cfg))

    def ensure_session(self, name):
        """Create session `name` detached unless it exists. Losing the creation
        race to another creator (rig's boot job, a second ctmux) is success."""
        rc, _ = run(["tmux", "has-session", "-t", "=" + name], stderr=Stdio.DEVNULL)
        if rc == 0:
            return
        rc, text = run(["tmux", "new-session", "-d", "-s", name, "-c", self.home], stdout=Stdio.PIPE, stderr=Stdio.MERGE)
        if rc == 0:
            self.load_keys()
            return
        rc, _ = run(["tmux", "has-session", "-t", "=" + name], stderr=Stdio.DEVNULL)
        if rc != 0:
            say(f"ctmux: could not create tmux session '{name}': {sh_out(text)}")
            sys.exit(1)

    def load_keys(self):
        """Source ctmux's key bindings (linux/tmux.conf: Ctrl+Tab = next window, ...) and then the
        user's ~/.config/ctmux/tmux.conf into the running server. They are global key bindings
        (re-binding is idempotent), so this runs on every attach and on session creation. A
        missing user file is skipped; a syntax error in either file is tmux's own message on stderr."""
        confs = [KEYS_CONF]
        user = os.path.join(self.home, ".config", "ctmux", "tmux.conf")
        if os.path.exists(user):
            confs.append(user)
        for conf in confs:
            run(["tmux", "source-file", conf])

    @staticmethod
    def need_terminal():
        """Attaching needs a terminal unless we are already inside tmux (then it is a switch-client)."""
        if not os.environ.get("TMUX") and not (os.isatty(0) and os.isatty(1)):
            say("ctmux: attaching needs a terminal — fix: run ctmux in a terminal, or use 'ctmux ensure' to only create the session")
            sys.exit(1)

    def go(self, session, window=""):
        """Hand the terminal to `session` (optionally at window index `window`):
        switch the current client when already inside tmux, attach otherwise.
        Unlike the cmux backend this does select the window — the picker row was
        chosen on purpose and there is no other mirror to protect, only the
        user's own choice. exec: no process left behind, tmux's exit status is
        ctmux's."""
        verb = "switch-client" if os.environ.get("TMUX") else "attach-session"
        self.need_terminal()
        self.load_keys()
        argv = ["tmux"]
        if window:
            argv += ["select-window", "-t", f"={session}:{window}", ";"]
        util.exec_replace(argv + [verb, "-t", "=" + session])

    def new_session(self, name=""):
        if not name:
            import datetime

            name = "ws-" + datetime.datetime.now().strftime("%H%M%S")
        name = sanitize(name)
        rc, text = run(["tmux", "new-session", "-d", "-s", name, "-c", self.home], stdout=Stdio.PIPE, stderr=Stdio.MERGE)
        if rc != 0:
            rc, _ = run(["tmux", "has-session", "-t", "=" + name], stderr=Stdio.DEVNULL)
            if rc != 0:
                say(f"ctmux: could not create tmux session '{name}': {sh_out(text)}")
                sys.exit(1)
            say(f"ctmux: session '{name}' already exists, attaching to it.")
        self.go(name)

    def picker(self):
        if not have("fzf"):
            say(f"ctmux: fzf not found — fix: {pkg_hint('fzf')}")
            sys.exit(1)
        rc, rows = run(["tmux", "list-windows", "-a", "-F", LIST_FORMAT], stdout=Stdio.PIPE, stderr=Stdio.DEVNULL)
        rows = sh_out(rows) if rc == 0 else ""
        menu = (rows + "\n" if rows else "") + "+ new session\n"
        rc, sel = run(
            ["fzf", "--prompt=ctmux> ", "--height=50%", "--reverse", "--header=enter: attach to this session + window"],
            stdin=menu,
            stdout=Stdio.PIPE,
        )
        if rc != 0:
            sys.exit(0)
        sel = sh_out(sel)
        if not sel:
            sys.exit(0)
        if sel == "+ new session":
            util.out("new session name: ", end="")
            self.new_session(sys.stdin.readline().strip(" \t\n"))
            return
        # tmux stores a tab in a session name as the two characters "\\t", so the
        # first real tab in a row is always the session/window separator.
        session, _, rest = sel.partition("\t")
        self.go(session, rest.split(" ")[0])

    def here(self, directory):
        """`ctmux here DIR`: a new window in DIR in the canonical session, then attach (the new window is
        the current one). What "Open in Terminal" runs, so the shell lands in the folder that was asked for."""
        session = self.canonical_session()
        self.need_terminal()  # before the window exists: a retry without a terminal must not leak windows
        self.ensure_session(session)
        directory = os.path.abspath(directory) if directory else ""
        if directory and os.path.isdir(directory):
            run(["tmux", "new-window", "-t", f"={session}:", "-c", directory])
        self.go(session)

    def ensure(self):
        self.ensure_session(self.canonical_session())

    def bare(self):
        session = self.canonical_session()
        self.ensure_session(session)
        self.go(session)
