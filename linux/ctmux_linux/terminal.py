"""`ctmux terminal` — open ctmux in a terminal window; the entry the desktop uses as
"the terminal" (ctmux.desktop, listed first in ~/.config/xdg-terminals.list).

  ctmux terminal                  a terminal window running `ctmux` (the canonical session)
  ctmux terminal -e CMD [ARGS]    CMD in a terminal window (what xdg-terminal-exec passes for
                                  "run this in a terminal"); a bare login shell means "open a
                                  terminal", so it becomes ctmux, anything else runs as asked
  ctmux terminal --title T --working-directory D -e CMD ...   the options xdg-terminal-exec
                                  passes (`--` also starts the command)

cmux has no Linux build, so the window is a terminal emulator running ctmux. Ghostty is the
first choice (cmux is Ghostty-based, and it is the only one that can send Ctrl+Tab to tmux:
see linux/ghostty.conf); then Ptyxis, GNOME Terminal, x-terminal-emulator. $CTMUX_TERMINAL
(a command name) overrides the choice.
"""
import os
import sys

from .util import LINUX_DIR, exec_replace, say, which

SHELLS = ("sh", "bash", "zsh", "fish", "dash", "ksh")
GHOSTTY_CLASS = "com.ctmux.Terminal"
GHOSTTY_CONF = os.path.join(LINUX_DIR, "ghostty.conf")
OPTIONS = {"--title": "title", "-T": "title", "--working-directory": "cwd", "-d": "cwd"}


class UsageError(Exception):
    pass


def parse(args):
    """-> (command list, title, working directory). `-e` and `--` start the command; an option
    without a value is an error, never a command."""
    found = {"title": "", "cwd": ""}
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "--"):
            return args[i + 1 :], found["title"], found["cwd"]
        name, eq, value = a.partition("=")
        if name in OPTIONS:
            if not eq:
                if i + 1 >= len(args):
                    raise UsageError("ctmux terminal: %s needs a value" % a)
                i += 1
                value = args[i]
            found[OPTIONS[name]] = value
            i += 1
            continue
        if a.startswith("-") and a != "-":
            raise UsageError("ctmux terminal: unknown option %s (known: --title, --working-directory; -e or -- starts the command)" % a)
        return args[i:], found["title"], found["cwd"]
    return [], found["title"], found["cwd"]


SHELL_FLAGS = ("-l", "--login", "-i")


def is_bare_shell(cmd):
    """No command, or just a shell (optionally `-l`): the caller wants "a terminal"."""
    if not cmd:
        return True
    return os.path.basename(cmd[0]).lstrip("-") in SHELLS and all(a in SHELL_FLAGS for a in cmd[1:])


SELF_REFERENCES = ("ctmux", "xdg-terminal-exec")  # forcing these would start ctmux terminal again, forever


class EmulatorError(Exception):
    pass


def refers_to_ctmux(path):
    """True when `path` (after symlinks) is ctmux or xdg-terminal-exec: starting it would loop."""
    return os.path.basename(os.path.realpath(path)) in SELF_REFERENCES or os.path.basename(path) in SELF_REFERENCES


def pick(env):
    """Name of the terminal emulator to use, or None; EmulatorError for a $CTMUX_TERMINAL that cannot be used."""
    forced = env.get("CTMUX_TERMINAL")
    if forced:
        found = which(forced)
        if found and refers_to_ctmux(found) or os.path.basename(forced) in SELF_REFERENCES:
            raise EmulatorError("CTMUX_TERMINAL=%s would start ctmux terminal again; name a real terminal emulator" % forced)
        if not found:
            raise EmulatorError("CTMUX_TERMINAL=%s is not on PATH" % forced)
        return forced
    for name in ("ghostty", "ptyxis", "gnome-terminal", "x-terminal-emulator"):
        found = which(name)
        if found and not refers_to_ctmux(found):
            return name
    return None


# emulator -> (leading args, title option, working-directory option, command separator)
EMULATORS = {
    "ghostty": (["--class=" + GHOSTTY_CLASS, "--config-file=" + GHOSTTY_CONF], "--title=%s", "--working-directory=%s", ["-e"]),
    "ptyxis": (["--new-window"], "--title=%s", "--working-directory=%s", ["--"]),
    "gnome-terminal": ([], "--title=%s", "--working-directory=%s", ["--"]),
}


def build_argv(emulator, cmd, title, cwd):
    """The emulator's command line; an emulator not in the table gets the xterm convention (-e)."""
    lead, title_opt, cwd_opt, sep = EMULATORS.get(os.path.basename(emulator), ([], None, None, ["-e"]))
    argv = [emulator] + lead
    if title and title_opt:
        argv.append(title_opt % title)
    if cwd and cwd_opt:
        argv.append(cwd_opt % cwd)
    return argv + sep + cmd


def main(args, env=None):
    env = os.environ if env is None else env
    try:
        cmd, title, cwd = parse(args)
    except UsageError as exc:
        say(str(exc))
        return 2
    if cwd:
        cwd = os.path.abspath(cwd)
    ctmux = os.path.join(LINUX_DIR, "ctmux")  # this checkout's launcher, not whichever ctmux is first on PATH
    if is_bare_shell(cmd):
        cmd = [ctmux, "here", cwd] if cwd else [ctmux]  # "Open in Terminal" in a directory: a window there
    try:
        emulator = pick(env)
    except EmulatorError as exc:
        say("ctmux terminal: " + str(exc))
        return 1
    if emulator is None:
        say("ctmux: no terminal emulator found — fix: sudo apt install ghostty (or ptyxis)")
        return 1
    if cwd:
        try:
            os.chdir(cwd)  # emulators without a directory option start in the launch cwd
        except OSError as exc:
            say("ctmux terminal: cannot use working directory %s: %s" % (cwd, exc.strerror))
            return 1
    exec_replace(build_argv(emulator, cmd, title, cwd))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
