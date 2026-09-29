# ctmux

Wrapper around cmux's "Remote tmux" (`tmux -CC` control mode) feature, looped
back to the local tmux server over SSH to `localhost`. Gives cmux's native
split/tab/selection UI over an existing tmux session — session → workspace,
window → tab, pane → native split, plain drag = native selection (Shift only
when the app inside a pane captures the mouse itself) — without changing
anything about the tmux server: rig/resurrect/continuum/tg/Moshi keep working
unchanged, this just adds another client to the same server.

Not a fork of cmux, and not something upstream (a personal terminal-choice
workflow, not a general-purpose feature) — a standalone wrapper repo, personal
to this machine's setup.

## Why SSH to localhost

cmux's control-mode integration is built exclusively as an SSH feature (their
own naming choice — the protocol itself doesn't care local vs remote).
Looping it to `127.0.0.1` is the only way to get real window→tab / pane→split
mapping today without forking cmux or Ghostty.

## Install

```sh
./install.sh
```

Symlinks `bin/ctmux` onto `~/.local/bin/ctmux`. The login LaunchAgent (auto
re-mirror at login) is opt-in per machine — off by default. To enable it on a
machine, set `enabled: true` on its `tools.items.ctmux` entry in
`~/.config/rig/config.yaml`:

```yaml
tools:
  items:
    ctmux:
      repo: ~/xp/ctmux-cli
      enabled: true
```

then re-run `install.sh`. `enabled` is a standard `tools.items.<name>` key,
so `rig apply`/`rig status` validate the config as usual.

## Usage

```sh
ctmux            # ensure the mirror is up, launch/focus cmux
ctmux ls          # fzf picker over session:window pairs
ctmux new [name]  # create a new tmux session (auto-named if omitted), mirror it
ctmux ensure      # idempotent mirror-check only (used by the LaunchAgent)
```

## Prerequisites

- macOS Remote Login (`System Settings > General > Sharing > Remote Login`).
- Key-based SSH to `localhost` with no password prompt: this repo's install
  does not set this up for you (it's a one-time, security-relevant local
  change) — verify with `ssh -o BatchMode=yes localhost true`. If it fails:
  ```sh
  printf 'from="127.0.0.1,::1" %s ctmux-loopback\n' "$(cat ~/.ssh/id_ed25519.pub | awk '{print $1" "$2}')" >> ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys
  ```
  and add a `Host localhost` block to `~/.ssh/config` so a `Host *` block
  elsewhere in the file (a default `ProxyJump`/`LocalForward`/etc.) can't
  silently apply to this persistent automation SSH connection, and so
  cmux's first connection doesn't stall on a host-key prompt:
  ```
  Host localhost
      HostName 127.0.0.1
      IdentityFile ~/.ssh/id_ed25519
      IdentitiesOnly yes
      StrictHostKeyChecking accept-new
      ProxyJump none
      ClearAllForwardings yes
  ```
- cmux (`brew install --cask cmux`). The socket must be reachable from
  outside cmux's own terminals: `ctmux` expects
  `~/.config/cmux/cmux.json` → `automation.socketControlMode: "password"`
  with `automation.socketPassword` set (`ctmux` reads the password from
  there). If password mode is on but the password is missing — cmux's app
  launch drops it, upstream bug
  [manaflow-ai/cmux#8372](https://github.com/manaflow-ai/cmux/issues/8372) —
  `ctmux` generates a fresh random one, writes it back (mode 0600, every
  other key kept) and runs `cmux reload-config`. It never restores the
  password from cmux's `cmux.*.bak` backups. This self-heal is a workaround
  and can be removed once that upstream bug is fixed. Anything it can't
  fix is reported as one line naming the problem and the fix command.
- The "Remote tmux" beta flag has no `cmux.json` key — it's a UserDefaults
  key (`com.cmuxterm.app`, `remoteTmux.beta.enabled`), read synchronously per
  call. `ctmux` flips it itself the first time it hits the "disabled" error;
  no manual click needed.

## Known costs

- The mirror streams every pane's output through the SSH loopback
  continuously. A busy TUI pane (an agent harness like omp running) adds up:
  observed ~95 MB of `total_output_bytes` for one session in well under an
  hour of active use. Bandwidth/CPU cost of this approach, not a leak.
- Window-level (tab-level) focus inside a mirrored session isn't wired up —
  `ctmux` focuses the right cmux *workspace* (session), not necessarily the
  exact tmux *window* within it. Fixing this needs a documented way to select
  a specific tab/surface by tmux window index, which cmux's CLI does not
  currently expose for the Remote tmux surfaces.
- `ctmux new`/`ctmux ls` never call `tmux select-window` or otherwise touch
  tmux's own current-window state — that's shared by every attached client,
  including a live outer terminal — deliberately, to avoid yanking your
  actual terminal's view out from under you.

## Tests

```sh
bash tests/ctmux_test.sh
```

Black-box tests against stub `cmux`/`pgrep`/`open`/`ssh`/`tmux` binaries and a
fixture `HOME`; they never touch the real `~/.config/cmux` or the live cmux app.
