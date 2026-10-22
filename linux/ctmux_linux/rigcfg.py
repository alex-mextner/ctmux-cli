"""The canonical tmux session name, from rig's own config.

rig's anti_sprawl block (`tmux.anti_sprawl.session` in ~/.config/rig/config.yaml)
names the one session its attach-or-create entry script uses; ctmux reads the
same value so the two never drift. No PyYAML (stdlib only, and importing it
would dominate ctmux's startup): a small indentation-based reader for the
block-style YAML rig writes. Flow style (`tmux: {anti_sprawl: {session: x}}`),
anchors, and a missing file or key all mean "main".
"""
DEFAULT = "main"


def _strip_comment(value):
    """`x  # note` -> `x` (a # only starts a comment after whitespace)."""
    cuts = [i for i in (value.find(" #"), value.find("\t#")) if i >= 0]
    return value[: min(cuts)] if cuts else value


def canonical_session(path):
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return DEFAULT
    in_tmux = in_anti = False
    anti_indent = 0
    for line in lines:
        body = line.lstrip()
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(body)
        if indent == 0:
            in_tmux = body.startswith("tmux:")
            in_anti = False
            continue
        if not in_tmux:
            continue
        if in_anti and indent <= anti_indent:
            in_anti = False
        if body.startswith("anti_sprawl:"):
            in_anti, anti_indent = True, indent
        elif in_anti and body.startswith("session:"):
            value = _strip_comment(body[len("session:"):].strip()).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif value[:1] in "\"'":
                value = value[1:]
            if value:
                return value
    return DEFAULT
