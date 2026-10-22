# ctmux

One command set — `ctmux`, `ctmux ls`, `ctmux new`, `ctmux ensure` — for working with
tmux sessions as workspaces. What runs depends on the OS:

- **macOS**: `bin/ctmux`, a shell script that is a wrapper around cmux's "Remote tmux"
  (`tmux -CC` control mode) feature, looped back to the local tmux server over SSH to
  `localhost`. Gives cmux's native split/tab/selection UI over an existing tmux session —
  session → workspace, window → tab, pane → native split, plain drag = native selection
  (Shift only when the app inside a pane captures the mouse itself) — without changing
  anything about the tmux server: rig/resurrect/continuum/tg/Moshi keep working unchanged,
  this just adds another client to the same server.
- **Linux** (Ubuntu tested): `linux/ctmux`, a small Python program (stdlib only) with the
  same four commands over **plain tmux**. cmux is a macOS-only app with no Linux build, and
  a Linux terminal already renders tmux windows and panes itself, so what is left is the
  session-level workflow: pick, create, ensure, attach. No SSH, no sshd, no launchd, no cmux.

Not a fork of cmux, and not something upstream (a personal terminal-choice workflow, not a
general-purpose feature) — a standalone wrapper repo, personal to this machine's setup.

## What runs where

| | macOS (`bin/ctmux`, shell, cmux) | Linux (`linux/ctmux`, Python, tmux) |
|---|---|---|
| `ctmux` | mirror the canonical session into cmux, launch/focus cmux | ensure the canonical session exists, attach to it (`switch-client` when already inside tmux) |
| `ctmux ls` | fzf over `session  window` pairs; Enter mirrors + focuses that **session** | same picker; Enter attaches to that session **and selects that window** |
| `ctmux new [name]` | create a session (`ws-HHMMSS` if unnamed), mirror, focus | create a session (same naming) or open it if it exists; attach / switch |
| `ctmux ensure` | idempotent mirror check; launches cmux if needed (the login LaunchAgent uses this) | create the canonical session detached if missing; never needs a terminal |
| Needs | tmux, fzf, python3, cmux, Remote Login + key-based SSH to localhost | tmux, fzf (for `ls`), python3 ≥ 3.9 |
| Login hook | opt-in LaunchAgent (`launchd/`) | none (see [#11](https://github.com/alex-mextner/ctmux-cli/issues/11)) |

`install.sh` picks the right entry point: on macOS `~/.local/bin/ctmux` links to `bin/ctmux`
(unchanged by the Linux work), on Linux to `linux/ctmux`. There is no backend switch:
cmux does not exist on Linux, and macOS keeps its script as it was.

On Linux the canonical session is `tmux.anti_sprawl.session` from `~/.config/rig/config.yaml`
(the value rig's own attach-or-create entry uses), else `main`. tmux rewrites `.` and `:` in
session names to `_`; the Linux ctmux does the same up front so a name you type is always the
name tmux finds again. Attaching needs a terminal: without one (a script, a service)
`ctmux ensure` is the command to use; `ctmux`, `ctmux new` and `ctmux ls` stop with
`attaching needs a terminal — fix: …` instead. tmux's own control-mode UI (`tmux -CC`, which
iTerm2 speaks on macOS) is not wired in: a plain tmux client is what you get on Linux.

## Install

### macOS

```sh
git clone https://github.com/alex-mextner/ctmux-cli ~/xp/ctmux-cli
cd ~/xp/ctmux-cli && ./install.sh
```

Symlinks `bin/ctmux` to `~/.local/bin/ctmux` and advertises the agent skill (below).
It installs **no packages** on macOS (as before): it prints a `note:` for a missing `tmux`,
`fzf` or cmux (`brew install tmux fzf`, `brew install --cask cmux`). The login LaunchAgent
(auto re-mirror at login) is opt-in per machine — off by default. To enable it, set
`enabled: true` on the machine's `tools.items.ctmux` entry in `~/.config/rig/config.yaml`:

```yaml
tools:
  items:
    ctmux:
      repo: ~/xp/ctmux-cli
      enabled: true
```

then re-run `install.sh`. `enabled` is a standard `tools.items.<name>` key, so
`rig apply`/`rig status` validate the config as usual. Then follow
[the macOS prerequisites](#macos-prerequisites).

### Ubuntu / Linux

```sh
git clone https://github.com/alex-mextner/ctmux-cli ~/xp/ctmux-cli
cd ~/xp/ctmux-cli && ./install.sh
```

Installs `tmux` and `fzf` only when missing: `apt-get install` when you are root or have
`sudo` (a password prompt only when run from a terminal), Homebrew if `apt` is not usable,
otherwise it stops and prints the exact `sudo apt install …` to run. It never touches cmux,
`osascript` or launchd. Then it symlinks `linux/ctmux` to `~/.local/bin/ctmux` (put that on
`PATH`) and advertises the agent skill. Nothing else to configure.

`install.sh` is a 29-line shim around `install.py` (stdlib Python ≥ 3.9, `python3` on `PATH`).
`curl … | bash` also works: with no checkout next to the script it clones
`https://github.com/alex-mextner/ctmux-cli.git` into
`${XDG_DATA_HOME:-~/.local/share}/ctmux-cli` and runs the installer from there.

### `--check` and `--dry-run`

```sh
./install.sh --check     # change nothing; exit 0 only if everything is installed correctly
./install.sh --dry-run   # print what would be done; change nothing
```

`--check` verifies the dependencies (Linux), that `~/.local/bin/ctmux` points at *this*
checkout's entry point for the OS, that the agent skill files match the repo's, and (macOS)
the LaunchAgent state implied by the rig config. It prints one `ok:`/`MISSING:` line per
item. Re-running `install.sh` is idempotent: only what is missing or stale is touched.

### rig

[rig](https://github.com/alex-mextner/rig-cli) installs ctmux declaratively: list it under
`tools.items` (the repo defaults to `~/xp/ctmux-cli`) and `rig apply` runs this repo's
`install.sh`, which does everything above for the OS it runs on — rig itself needs no OS
filter and no `brew:` entry for ctmux (that key would force Homebrew onto Linux; the
installer already picks apt or Homebrew itself).

```yaml
tools:
  enabled: true
  items:
    ctmux: {}          # macOS: add `enabled: true` + repo to opt into the LaunchAgent, see above
```

rig considers the tool provisioned when `ctmux` is on `PATH` **and** its skill blurb exists
(`~/.agents/skills/.blurbs/ctmux.md`); the installer writes both (from `skill/`), so once
installed `rig apply` skips it, and `rig status` flags it again if either goes missing.

## Usage

```sh
ctmux            # canonical session: attach (Linux) / mirror + launch cmux (macOS)
ctmux ls          # fzf picker over session:window pairs
ctmux new [name]  # create a new tmux session (auto-named if omitted) or open it
ctmux ensure      # idempotent, no terminal needed (the LaunchAgent uses this on macOS)
```

## Linux: efficiency

The Linux ctmux is Python, so the floor is the interpreter's startup. `linux/ctmux` is a
6-line `sh` launcher that starts it as `python3 -I -S` (isolated, no `site` import — nothing
from site-packages is needed), which roughly halves startup; the package imports only what a
command needs (no `json`/`re`/`subprocess`/`tempfile` on the hot path — a test pins that),
runs external commands with `os.posix_spawn`, and never forks `uname`. Measured with
`tests/bench.py` on a private tmux server with 51 sessions / 101 windows (Ubuntu 26.04,
16 cores, Python 3.14.8, tmux 3.6a; median of 30 runs; the machine was under memory
pressure, so absolute numbers are pessimistic):

| command | median | p95 |
|---|---|---|
| `python3 -I -S -c pass` (the floor) | 20 ms | 23 ms |
| `tmux has-session` | 4.3 ms | 6 ms |
| `ctmux ensure` (session exists) | 39 ms | 44 ms |
| `ctmux ls` (list + fzf stub) | 43 ms | 48 ms |
| `ctmux new <name>` | 41–48 ms | 60 ms |
| `ctmux ensure`, cold (no tmux server yet) | 61 ms | — |

Everything stays well under 100 ms. Timings are machine-dependent — rerun
`python3 tests/bench.py` after touching a hot path (`N_SESSIONS=200` to scale up); it pins
every tmux call to its own private socket and refuses to run inside tmux.

## Agent skill

`skill/SKILL.md` and `skill/blurb.md` describe the commands to coding agents. The installer
copies them to `~/.agents/skills/ctmux/SKILL.md` and `~/.agents/skills/.blurbs/ctmux.md`.


## macOS: why SSH to localhost

cmux's control-mode integration is built exclusively as an SSH feature (their
own naming choice — the protocol itself doesn't care local vs remote).
Looping it to `127.0.0.1` is the only way to get real window→tab / pane→split
mapping today without forking cmux or Ghostty.

## macOS: prerequisites

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
  and reads the password from `automation.socketPassword` there. If
  password mode is on but the password is missing — cmux's app launch drops
  it, upstream bug
  [manaflow-ai/cmux#8372](https://github.com/manaflow-ai/cmux/issues/8372),
  which after a reboot breaks cmux's auto-resumed `cmux restore tmux main`
  with `auth_required` — `ctmux ensure` generates a fresh random one,
  writes it back (mode 0600, every other key kept, comments dropped) and
  runs `cmux reload-config`. It never restores the password from cmux's
  `cmux.*.bak` backups. This self-heal is a workaround and can be removed
  once that upstream bug is fixed. Anything it can't fix is reported as one
  line naming the problem and the fix command. Overlapping runs (the login
  agent and a manual `ctmux`) are serialized by an `flock` on
  `~/.config/cmux/.ctmux-heal.lock`, so they generate and load one password;
  the kernel drops the lock with its holder, so a killed ctmux leaves nothing
  stale behind. A holder that hangs for over 90 s is not waited for any
  longer (a hung ctmux must not lock the others out).
- The "Remote tmux" beta flag has no `cmux.json` key — it's a UserDefaults
  key (`com.cmuxterm.app`, `remoteTmux.beta.enabled`), read synchronously per
  call. `ctmux` flips it itself the first time it hits the "disabled" error;
  no manual click needed.

## macOS: known costs

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

## Layout

```
bin/ctmux          macOS: the shell script (cmux), unchanged by the Linux work
launchd/           macOS login LaunchAgent template
linux/ctmux        Linux: 6-line sh launcher -> python3 -I -S, resolves its own symlink
linux/ctmux_linux/ the Linux package (stdlib only): cli, tmux_backend, rigcfg, util
install.sh         29-line shim (rig runs `bash <repo>/install.sh`)
install.py         the installer: OS gating, deps, --check, --dry-run, skill
skill/             agent skill text installed by install.py
tests/             ctmux_test.sh (macOS shell suite) + Python unittest suites
```

New code in this repo family is Python by owner policy (a pre-commit hook blocks added or
changed shell scripts over 40 lines), which is why the Linux side and the installer are
Python while `bin/ctmux` and its shell suite are left exactly as they were.

## What is still macOS-only

cmux itself and everything built on it: the workspace mirroring, ctmux's cmux socket
password self-heal, the "Remote tmux" beta flag, the SSH-to-localhost prerequisites, and the
login LaunchAgent (`launchd/`). On Linux there is no cmux, so none of it applies and none of
it runs.

## Tests

```sh
bash tests/ctmux_test.sh                      # macOS only: bin/ctmux (real /usr/bin/pgrep, BSD stat)
python3 -m unittest discover -s tests -v      # Linux ctmux, installer, hygiene (Linux + macOS)
python3 tests/bench.py                        # Linux latency, by hand
shellcheck bin/ctmux tests/ctmux_test.sh install.sh linux/ctmux
```

The Python suites are stdlib `unittest`, black-box: external commands (`tmux`, `fzf`,
`uname`, `launchctl`, `brew`, `apt-get`, `sudo`, …) are stubs in a per-test bin dir that log
every call, and `HOME` is a fixture dir, so nothing real is touched. The Linux tests run the
real tmux, but only through a wrapper pinned to a private socket (`tmux -L ct<random> -f
/dev/null` under a per-test, existing `TMUX_TMPDIR`) that fakes `attach-session` /
`switch-client`; they refuse to run when `TMUX` is set and never go near the default socket.
`tests/test_install_parity.py` replays the macOS install scenarios recorded from the original
`install.sh` (`tests/golden/install_trace.json`, regenerated by
`tests/golden/generate_install.py`) against `install.py`.

### Verifying on a real Mac (not done yet)

The macOS *runtime* path is the unchanged shell script. The macOS *install* path
(`install.sh` → `install.py`) is a Python port that was only exercised with stubs on Linux,
plus the golden parity above. Checklist for a Mac with cmux (after `git pull` of this branch):

1. `bash tests/ctmux_test.sh` and `python3 -m unittest discover -s tests -v` — both green
   (the two real-`pgrep` tests only run on macOS).
2. `./install.sh --dry-run` prints only `would …` lines and changes nothing;
   `./install.sh --check` lists what is missing before the first install.
3. `./install.sh` — `ls -l ~/.local/bin/ctmux` points at `<repo>/bin/ctmux`; with
   `tools.items.ctmux.enabled: true` in `~/.config/rig/config.yaml`,
   `launchctl print gui/$(id -u)/com.ultra.ctmux-ensure` shows the agent and
   `~/Library/LaunchAgents/com.ultra.ctmux-ensure.plist` points at `~/.local/bin/ctmux`;
   without it no plist exists.
4. A second `./install.sh` changes nothing; `./install.sh --check` exits 0.
5. `ctmux ensure`, `ctmux`, `ctmux ls`, `ctmux new scratch` behave exactly as before.
