---
name: ctmux
description: >-
  Session-level tmux launcher: `ctmux` (attach to / create the canonical tmux
  session), `ctmux ls` (fzf picker over session:window pairs), `ctmux new [name]`
  (create or attach to a session), `ctmux ensure` (create the canonical session
  detached if missing, no terminal needed). On macOS it mirrors tmux sessions into
  cmux workspaces; on Linux it works over plain tmux. Use it instead of hand-typed
  `tmux new-session`/`attach` when the user wants their usual tmux workspace.
metadata:
  author: alex-mextner
  repo: https://github.com/alex-mextner/ctmux-cli
---

# ctmux — tmux sessions as workspaces

| Command | What it does |
|---|---|
| `ctmux` | Ensure the canonical session (`tmux.anti_sprawl.session` in rig's config, else `main`) exists, then attach / switch to it (macOS with cmux: mirror it into cmux). |
| `ctmux ls` | fzf over every `session  window` pair; Enter opens that session (tmux backend: at that window); `+ new session` creates one. |
| `ctmux new [name]` | Create session `name` (auto-named `ws-HHMMSS`), or open it if it already exists. tmux turns `.` and `:` in names into `_`. |
| `ctmux ensure` | Idempotent, never needs a terminal: makes sure the canonical session exists (macOS/cmux: also that the mirror is up). |

On Linux, attaching needs a terminal (inside tmux it switches the current client). From an
agent shell without a terminal only `ctmux ensure` works; `ctmux new` creates the
session but then exits 1 ("needs a terminal").

On macOS `ctmux` is the cmux wrapper (`bin/ctmux`); on Linux it is a small Python program
over plain tmux (`linux/ctmux`) — same commands, installed by `install.sh` per OS.
