#!/usr/bin/env bash
# tests/ctmux_test.sh — black-box tests for bin/ctmux against a fake world.
#
# Every external dependency (cmux, pgrep, open, ssh, nc, tmux, defaults,
# sleep) is a stub in a per-test bin dir, and HOME points at a per-test
# fixture dir, so the real ~/.config/cmux and the live cmux app are never
# touched (CMUX_SOCKET_PATH also points into the fixture, in case a real
# cmux binary is ever reached). The fake `cmux` models the socket auth: an
# authenticated command succeeds only when CMUX_SOCKET_PASSWORD equals the
# password the "app" has loaded ($W/app_pw), which it loads from cmux.json
# on `cmux reload-config` (or on every call with $W/app_watches_cfg, like
# the real app's file watcher).
#
# Run: bash tests/ctmux_test.sh
# Fixture JSON contains literal "$schema" keys, single-quoted on purpose.
# shellcheck disable=SC2016
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTMUX="$ROOT/bin/ctmux"
PASS=0
FAIL=0
FAILED_NAMES=()

fail() {
	echo "    FAIL: $*"
	return 1
}

# ---------- fake world ----------

new_world() {
	W="$(mktemp -d "${TMPDIR:-/tmp}/ctmux-test.XXXXXX")"
	mkdir -p "$W/bin" "$W/home/.config/cmux"
	CFG="$W/home/.config/cmux/cmux.json"
	# The stub dir first; Homebrew's dirs listed explicitly so ctmux's own
	# "add Homebrew to PATH" step has nothing to prepend in front of the stubs.
	TEST_PATH="$W/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
	: >"$W/calls"
	touch "$W/cmux_up"

	cat >"$W/bin/cmux" <<'SH'
#!/bin/bash
echo "cmux $1" >>"$W/calls"
# fd 9 is ctmux's heal lock: a cmux child that inherits it could keep the lock
# alive after ctmux itself died.
{ : >&9; } 2>/dev/null && echo "$*" >>"$W/fd9_leaks"
echo "cmux $*" >>"$W/calls_full"
surf_arg() { while [ $# -gt 0 ]; do [ "$1" = --surface ] && { echo "$2"; return; }; shift; done; }
cfg_pw() {
	python3 - "$HOME/.config/cmux/cmux.json" <<'PY'
import json, re, sys
try:
	t = re.sub(r'(?m)^\s*//.*$', '', open(sys.argv[1]).read())
	t = re.sub(r',(\s*[}\]])', r'\1', t)
	print(json.loads(t).get("automation", {}).get("socketPassword") or "", end="")
except Exception:
	pass
PY
}
[ -f "$W/app_watches_cfg" ] && cfg_pw >"$W/app_pw"
if [ "$1" = reload-config ]; then
	[ -f "$W/reload_fail" ] && { echo "Error: reload failed" >&2; exit 1; }
	# $W/gate_reload: hold the reload (the app has not loaded the new password
	# yet) until the test creates $W/release_reload — a deterministic window in
	# which a second ctmux run can overlap the first one's heal.
	if [ -f "$W/gate_reload" ]; then
		: >"$W/reload_started"
		for _ in $(seq 1 400); do [ -f "$W/release_reload" ] && break; /bin/sleep 0.05; done
	fi
	cfg_pw >"$W/app_pw"
	exit 0
fi
[ -f "$W/socket_down" ] && { echo "Error: Failed to connect to socket" >&2; exit 1; }
# The app's launch drops the saved password (upstream cmux#8372) just after
# ctmux first read it.
if [ "$1" = ping ] && [ -f "$W/drop_pw_on_first_ping" ]; then
	rm "$W/drop_pw_on_first_ping"
	python3 - "$HOME/.config/cmux/cmux.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["automation"].pop("socketPassword", None)
json.dump(d, open(sys.argv[1], "w"))
PY
	: >"$W/app_pw"
fi
if [ -z "${CMUX_SOCKET_PASSWORD:-}" ] || [ "$CMUX_SOCKET_PASSWORD" != "$(cat "$W/app_pw" 2>/dev/null)" ]; then
	echo "Error: auth_required: Authentication required. Send auth <password> first." >&2
	exit 1
fi
# Surface catalog (fixtures written by fx_surface): tree, top, per-surface
# resume binding and screen; `send` is only recorded.
case "$1" in
	rpc) echo '{"attached":true,"started":true}' ;;
	tree) cat "$W/tree.json" 2>/dev/null ;;
	--json)
		if [ "$2" = top ]; then
			# $W/top_later.json: what `top` reports from its second call on (a
			# pane that went busy after the first look).
			echo x >>"$W/top.calls"
			if [ -f "$W/top_later.json" ] && [ "$(wc -l <"$W/top.calls")" -ge 2 ]; then
				cat "$W/top_later.json"
			else
				cat "$W/top.json" 2>/dev/null
			fi
		fi
		;;
	surface) cat "$W/resume.$(surf_arg "$@")" 2>/dev/null ;;
	read-screen) cat "$W/screen.$(surf_arg "$@")" 2>/dev/null ;;
	send)
		# $W/gate_send: hold the typing until the test creates $W/release_send.
		if [ -f "$W/gate_send" ]; then
			: >"$W/send_started"
			for _ in $(seq 1 400); do [ -f "$W/release_send" ] && break; /bin/sleep 0.05; done
		fi
		echo "$*" >>"$W/sent"
		# What the terminal does: the typed command shows up on the screen.
		typed=${*#send --surface * }
		# $W/no_echo: a terminal that is slow to show it (or never does).
		[ -f "$W/no_echo" ] || printf '> %s\n' "${typed%\\n}" >>"$W/screen.$(surf_arg "$@")"
		;;
esac
exit 0
SH
	cat >"$W/bin/pgrep" <<'SH'
#!/bin/bash
echo "pgrep $*" >>"$W/calls"
[ -f "$W/cmux_up" ]
SH
	cat >"$W/bin/open" <<'SH'
#!/bin/bash
echo "open $*" >>"$W/calls"
touch "$W/cmux_up"
SH
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/nc"
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/ssh"
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/tmux"
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/ps"
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/defaults"
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/sleep"
	chmod +x "$W/bin/"*
	export W
}

run_ctmux() {
	HOME="$W/home" PATH="$TEST_PATH" CMUX_SOCKET_PATH="$W/cmux.sock" \
		env -u CMUX_SOCKET_PASSWORD -u CMUX_SOCKET_CAPABILITY \
		"$CTMUX" "$@" >"$W/out" 2>"$W/err"
	RC=$?
}

# Same fake world as run_ctmux, but in the background, with its own
# out/err/rc files ($W/out.NAME, $W/err.NAME, $W/rc.NAME); `wait` joins it.
run_ctmux_bg() {
	local name="$1"
	shift
	(
		HOME="$W/home" PATH="$TEST_PATH" CMUX_SOCKET_PATH="$W/cmux.sock" \
			env -u CMUX_SOCKET_PASSWORD -u CMUX_SOCKET_CAPABILITY \
			"$CTMUX" "$@" >"$W/out.$name" 2>"$W/err.$name"
		echo $? >"$W/rc.$name"
	) &
}

# Real sleeps on purpose: the stub dir shadows `sleep` with a no-op.
wait_for_file() { # path seconds
	local i
	for ((i = 0; i < $2 * 20; i++)); do
		[ -e "$1" ] && return 0
		/bin/sleep 0.05
	done
	return 1
}

write_cfg() { printf '%s\n' "$1" >"$CFG"; chmod 600 "$CFG"; }
app_has_pw() { printf '%s' "$1" >"$W/app_pw"; }
calls() { cat "$W/calls"; }
json_get() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {"d": d}))' "$CFG" "$1"; }
file_mode() { stat -L -f '%Lp' "$1"; }
line_count() { awk 'END{print NR}' "$1"; }

# ---- surface catalog fixtures (the fake cmux's terminals) ----

# fx_surface REF IDLE KIND ID SCREEN: one terminal surface. IDLE 1 = nothing
# but the shell runs in it, 0 = a tmux client is its foreground process. KIND
# empty = no resume binding.
fx_surface() {
	local ref="$1" idle="$2" kind="$3" id="$4" screen="$5"
	printf '%s\t%s\n' "$ref" "$idle" >>"$W/fx_surfaces"
	printf '%s\n' "$screen" >"$W/screen.$ref"
	if [ -n "$kind" ]; then
		printf '{"restore_record":{"kind":"%s","checkpoint_id":"%s"}}\n' "$kind" "$id" >"$W/resume.$ref"
	else
		printf '{"restore_record":null}\n' >"$W/resume.$ref"
	fi
	python3 - "$W" <<'PY'
import json, sys
w = sys.argv[1]
rows = [l.rstrip("\n").split("\t") for l in open(w + "/fx_surfaces")]
surfaces = [{"ref": r, "type": "terminal"} for r, _ in rows]
tree = {"windows": [{"ref": "window:1", "workspaces": [{"ref": "workspace:1", "panes": [{"ref": "pane:1", "surfaces": surfaces}]}]}]}
tops = [{"kind": "surface", "ref": r, "processes": [] if idle == "1" else [{"name": "tmux", "pid": 4242}],
         "foreground_pgids": [] if idle == "1" else [4242]} for r, idle in rows]
top = {"windows": [{"workspaces": [{"panes": [{"surfaces": tops}]}]}]}
json.dump(tree, open(w + "/tree.json", "w"))
json.dump(top, open(w + "/top.json", "w"))
PY
}

# A pane as cmux's auto-resume leaves it: the typed restore, its error, a prompt.
failed_restore_screen() { # kind id error
	printf 'Last login: Thu Oct  1 12:19:14 on ttys001\n\n> cmux restore %s %s\n%s\n\n~ > ' "$1" "$2" "$3"
}
AUTH_ERR='Error: auth_required: Authentication required. Send auth <password> first.'
SESSION_ID=11111111-2222-3333-4444-555555555555
CFG_OK='{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
sent() { cat "$W/sent" 2>/dev/null; }

PASSWORDLESS='{
  "$schema": "https://example.invalid/cmux.schema.json",
  "automation": {"socketControlMode": "password", "keepMe": 7},
  "app": {"theme": "dark"},
  "schemaVersion": 1
}'
LEAKED_BAK_PW="0123456789abcdef0123456789abcdef0123456789abcdef"

assert_healed() {
	local pw
	[ "$RC" -eq 0 ] || fail "exit $RC, stderr: $(cat "$W/err")" || return 1
	pw=$(json_get 'd["automation"]["socketPassword"]')
	[[ "$pw" =~ ^[0-9a-f]{48}$ ]] || fail "socketPassword is not 48 hex chars" || return 1
	[ "$pw" != "$LEAKED_BAK_PW" ] || fail "restored the (compromised) .bak password" || return 1
	[ "$(file_mode "$CFG")" = 600 ] || fail "mode $(file_mode "$CFG") != 600" || return 1
	[ "$(json_get 'd["automation"]["socketControlMode"]')" = password ] || fail "mode key lost" || return 1
	[ "$(json_get 'd["automation"]["keepMe"]')" = 7 ] || fail "automation.keepMe lost" || return 1
	[ "$(json_get 'd["app"]["theme"]')" = dark ] || fail "app.theme lost" || return 1
	[ "$(json_get 'd["schemaVersion"]')" = 1 ] || fail "schemaVersion lost" || return 1
	[ "$(json_get 'd["$schema"]')" = "https://example.invalid/cmux.schema.json" ] || fail "\$schema lost" || return 1
	calls | grep -qx 'cmux reload-config' || fail "cmux reload-config not called" || return 1
	calls | grep -A99 'cmux reload-config' | grep -qx 'cmux ssh-tmux' || fail "mirror (ssh-tmux) not completed after reload" || return 1
	! grep -qF "$pw" "$W/out" "$W/err" || fail "password printed" || return 1
}

# Exactly one stderr line, mentioning $1, containing a fix command, and never
# the raw cmux errors.
assert_one_actionable_line() {
	[ "$RC" -ne 0 ] || fail "expected non-zero exit" || return 1
	[ "$(line_count "$W/err")" = 1 ] || fail "stderr has $(line_count "$W/err") lines: $(cat "$W/err")" || return 1
	grep -q "$1" "$W/err" || fail "stderr lacks '$1': $(cat "$W/err")" || return 1
	grep -q 'fix: ' "$W/err" || fail "stderr has no 'fix: ' command: $(cat "$W/err")" || return 1
	! grep -qiE 'auth_required|socket not reachable after launch' "$W/err" || fail "raw error leaked: $(cat "$W/err")" || return 1
	! calls | grep -qx 'cmux ssh-tmux' || fail "continued to ssh-tmux after failure" || return 1
}

# ---------- #3: cmux-running detection ----------

test_running_cmux_is_not_relaunched() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	! calls | grep -q '^open' || fail "open called although cmux runs: $(calls)" || return 1
	calls | grep -q '^pgrep .*-a' || fail "pgrep not ancestor-inclusive (-a): $(calls)"
}

test_stopped_cmux_is_launched_exactly_once() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	rm -f "$W/cmux_up"
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ "$(calls | grep -c '^open -a cmux$')" = 1 ] || fail "open -a cmux called $(calls | grep -c '^open') times"
}

# The pgrep stub for the real-pgrep tests: forwards ctmux's exact flags to
# /usr/bin/pgrep and only counts a match on the fake processes' PIDs, so a
# real cmux running on the test machine can't affect the result.
use_real_pgrep() {
	cat >"$W/bin/pgrep" <<'SH'
#!/bin/bash
/usr/bin/pgrep "$@" | grep -qxFf "$W/fake_pids"
SH
}

# Real /usr/bin/pgrep: a process whose ANCESTOR is the cmux app (what every
# shell in a cmux terminal is) must see cmux as running. macOS pgrep hides
# the caller's ancestors unless -a is given.
test_real_pgrep_sees_cmux_ancestor() {
	[ -x /usr/bin/pgrep ] || { echo "    skip: no /usr/bin/pgrep"; return 0; }
	local app="$W/fake/cmux.app/Contents/MacOS"
	mkdir -p "$app"
	cat >"$app/cmux" <<SH
#!/bin/bash
# Fake cmux app: runs ensure_cmux_running as its own descendant.
echo \$\$ >"$W/fake_pids"
source "$CTMUX"
open() { echo "open \$*" >>"$W/calls"; }
ensure_cmux_running
SH
	chmod +x "$app/cmux"
	# Everything but pgrep stays stubbed, so nothing real is launched.
	use_real_pgrep
	rm -f "$W/cmux_up"
	HOME="$W/home" PATH="$TEST_PATH" "$app/cmux" >"$W/out" 2>"$W/err"
	RC=$?
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	! calls | grep -q '^open' || fail "open called from inside a cmux descendant"
}

# A running `cmux` CLI process (same process name as the app, e.g. a pane's
# `cmux restore tmux main`) must not count as the app being up — otherwise a
# dead app with a lingering CLI call is never relaunched.
test_cmux_cli_process_is_not_the_app() {
	[ -x /usr/bin/pgrep ] || { echo "    skip: no /usr/bin/pgrep"; return 0; }
	local cli="$W/fake/cmux.app/Contents/Resources/bin"
	mkdir -p "$cli"
	# A real binary run as `.../bin/cmux` (not a script), so pgrep sees the
	# name `cmux`, like the CLI's (/opt/homebrew/bin/cmux is a symlink too).
	ln -s /bin/sleep "$cli/cmux"
	"$cli/cmux" 30 &
	local cli_pid=$!
	echo "$cli_pid" >"$W/fake_pids"
	use_real_pgrep
	# Nothing makes the app appear, so this ends in "did not start in time".
	HOME="$W/home" PATH="$TEST_PATH" bash -c "source '$CTMUX'; open() { echo \"open \$*\" >>'$W/calls'; }; ensure_cmux_running" \
		>"$W/out" 2>"$W/err" || true
	kill "$cli_pid" 2>/dev/null || true
	wait "$cli_pid" 2>/dev/null || true
	[ "$(calls | grep -c '^open -a cmux$')" = 1 ] || fail "app not launched while only a cmux CLI process runs: $(calls)"
}

# ---------- #2: socket password self-heal ----------

test_heals_missing_password_ignoring_bak() {
	write_cfg "$PASSWORDLESS"
	printf '{"automation":{"socketControlMode":"password","socketPassword":"%s"}}\n' "$LEAKED_BAK_PW" \
		>"$W/home/.config/cmux/cmux.20260925T171749.bak"
	run_ctmux ensure
	assert_healed
}

test_heals_missing_password_without_bak() {
	write_cfg "$PASSWORDLESS"
	chmod 644 "$CFG"
	run_ctmux ensure
	assert_healed
}

test_heals_jsonc_config() {
	write_cfg '{
  // cmux writes JSONC
  "$schema": "https://example.invalid/cmux.schema.json",
  "automation": {"socketControlMode": "password", "keepMe": 7,},
  // trailing commas too
  "app": {"theme": "dark"},
  "schemaVersion": 1,
}'
	run_ctmux ensure
	assert_healed
}

test_heal_writes_through_symlink() {
	mkdir -p "$W/dotfiles"
	printf '%s\n' "$PASSWORDLESS" >"$W/dotfiles/cmux.json"
	ln -s "$W/dotfiles/cmux.json" "$CFG"
	run_ctmux ensure
	[ -L "$CFG" ] || fail "symlink replaced by a regular file" || return 1
	assert_healed
}

# Right after `open -a cmux` the app may still be about to drop the password
# ctmux just read: ctmux has to notice and heal then, not report "rejected".
test_heals_password_dropped_after_first_read() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	touch "$W/drop_pw_on_first_ping"
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[[ "$(json_get 'd["automation"]["socketPassword"]')" =~ ^[0-9a-f]{48}$ ]] || fail "password not regenerated" || return 1
	calls | grep -qx 'cmux ssh-tmux' || fail "mirror not run"
}

# cmux's file watcher picks the new password up by itself: a failing
# reload-config then doesn't matter, the ping decides.
test_heal_ok_when_reload_fails_but_cmux_picked_it_up() {
	write_cfg "$PASSWORDLESS"
	touch "$W/reload_fail" "$W/app_watches_cfg"
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	calls | grep -qx 'cmux ssh-tmux' || fail "mirror not run"
}

test_present_accepted_password_is_left_alone() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	local before
	before=$(cat "$CFG")
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ "$(cat "$CFG")" = "$before" ] || fail "config rewritten" || return 1
	! calls | grep -qx 'cmux reload-config' || fail "needless reload-config" || return 1
	calls | grep -qx 'cmux ssh-tmux' || fail "mirror not run"
}

test_unparseable_config() {
	write_cfg '{"automation": {"socketControlMode": "password",'
	local before
	before=$(cat "$CFG")
	run_ctmux ensure
	assert_one_actionable_line 'not valid JSON' || return 1
	[ "$(cat "$CFG")" = "$before" ] || fail "unparseable config was modified"
}

test_missing_config() {
	run_ctmux ensure
	assert_one_actionable_line 'cmux.json not found'
}

test_password_mode_off_and_no_password() {
	write_cfg '{"automation":{"socketControlMode":"cmuxOnly"}}'
	run_ctmux ensure
	assert_one_actionable_line 'socketControlMode' || return 1
	[ "$(json_get 'd["automation"].get("socketPassword", "")')" = "" ] || fail "wrote a password outside password mode"
}

test_unwritable_config_dir() {
	write_cfg "$PASSWORDLESS"
	chmod 500 "$W/home/.config/cmux"
	run_ctmux ensure
	chmod 700 "$W/home/.config/cmux"
	assert_one_actionable_line 'cannot write' || return 1
	grep -q 'chmod' "$W/err" || fail "fix is not a chmod: $(cat "$W/err")"
}

# The fix must name the directory that actually needs write permission: for
# a dotfiles symlink that is the link target's, not ~/.config/cmux.
test_unwritable_symlink_target_dir() {
	mkdir -p "$W/dotfiles"
	printf '%s\n' "$PASSWORDLESS" >"$W/dotfiles/cmux.json"
	ln -s "$W/dotfiles/cmux.json" "$CFG"
	chmod 500 "$W/dotfiles"
	run_ctmux ensure
	chmod 700 "$W/dotfiles"
	assert_one_actionable_line 'cannot write' || return 1
	local real_dir
	real_dir=$(cd -P "$W/dotfiles" && pwd)
	grep -qF "chmod u+w $real_dir " "$W/err" || fail "fix doesn't chmod the link target's dir: $(cat "$W/err")"
}

test_reload_fails() {
	write_cfg "$PASSWORDLESS"
	touch "$W/reload_fail"
	run_ctmux ensure
	assert_one_actionable_line 'reload-config' || return 1
	local pw
	pw=$(json_get 'd["automation"]["socketPassword"]')
	! grep -qF "$pw" "$W/out" "$W/err" || fail "password printed"
}

test_present_password_rejected() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw beef
	run_ctmux ensure
	assert_one_actionable_line 'rejected' || return 1
	! grep -q cafe "$W/out" "$W/err" || fail "password printed"
}

test_socket_unreachable() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	touch "$W/socket_down"
	run_ctmux ensure
	assert_one_actionable_line 'not reachable'
}

# ---------- #4: concurrent heals ----------

HEAL_LOCK_REL=".config/cmux/.ctmux-heal.lock"

# The login agent and a manual `ctmux` both see the password missing. The
# second must neither generate a second password nor ping with one cmux has
# not loaded yet: it waits for the first run's heal to finish.
test_concurrent_ensure_runs_agree_on_one_password() {
	write_cfg "$PASSWORDLESS"
	touch "$W/gate_reload"
	run_ctmux_bg a ensure
	wait_for_file "$W/reload_started" 10 || fail "run a never reached reload-config: $(cat "$W/err.a")" || return 1
	run_ctmux_bg b ensure
	# b has passed its cmux-running check (the second pgrep) and is about to
	# authenticate: it is either done soon (no lock) or queued behind a's lock.
	for _ in $(seq 1 200); do
		[ "$(calls | grep -c '^pgrep')" -ge 2 ] && break
		/bin/sleep 0.05
	done
	# Without the lock b runs its whole auth loop now and ends on a false
	# "rejected" (rc.b appears within a second or two); with it b is queued
	# behind a and rc.b stays absent until a lets go. Either way, a is released
	# only after b had its chance to fail.
	for _ in $(seq 1 100); do
		[ -e "$W/rc.b" ] && break
		/bin/sleep 0.05
	done
	touch "$W/release_reload"
	wait
	[ "$(cat "$W/rc.a")" = 0 ] || fail "run a exit $(cat "$W/rc.a"): $(cat "$W/err.a")" || return 1
	[ "$(cat "$W/rc.b")" = 0 ] || fail "run b exit $(cat "$W/rc.b"): $(cat "$W/err.b")" || return 1
	[ "$(cat "$W/app_pw")" = "$(json_get 'd["automation"]["socketPassword"]')" ] || fail "file and loaded password differ" || return 1
	[ "$(calls | grep -c '^cmux reload-config$')" = 1 ] || fail "password generated or loaded more than once: $(calls)" || return 1
	! grep -qF "$(cat "$W/app_pw")" "$W"/out.* "$W"/err.* || fail "password printed"
}

# What ctmux runs under the lock must not inherit it: a hung cmux child would
# keep the flock alive after its parent died.
test_children_of_the_locked_section_do_not_inherit_the_lock() {
	# Every command of the locked section, not just cmux: the pause between two
	# rounds (sleep) must not hold the lock either. A launch-time password drop
	# after the first ping makes ctmux run a second round, so it does sleep.
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	touch "$W/drop_pw_on_first_ping"
	cat >"$W/bin/sleep" <<'SH'
#!/bin/bash
echo "sleep $*" >>"$W/sleeps"
{ : >&9; } 2>/dev/null && echo "sleep $*" >>"$W/fd9_leaks"
exit 0
SH
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ -s "$W/sleeps" ] || fail "the scenario never reached a sleep" || return 1
	[ ! -s "$W/fd9_leaks" ] || fail "a command ran with the lock fd open: $(cat "$W/fd9_leaks")"
}

# A ctmux killed in the middle of a heal must not block later runs: the lock
# is an flock, which the kernel drops with its holder, so there is no stale
# lock to detect.
test_killed_lock_holder_does_not_block_later_runs() {
	write_cfg "$PASSWORDLESS"
	python3 - "$W/home/$HEAL_LOCK_REL" "$W/holder_ready" <<'PY' &
import fcntl, sys, time
f = open(sys.argv[1], "a")
fcntl.flock(f, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(600)
PY
	local holder=$!
	wait_for_file "$W/holder_ready" 10 || { kill "$holder"; fail "holder never took the lock"; return 1; }
	kill -9 "$holder"
	wait "$holder" 2>/dev/null
	SECONDS=0
	run_ctmux ensure
	assert_healed || return 1
	[ "$SECONDS" -lt 10 ] || fail "run blocked ${SECONDS}s behind a dead lock holder"
}

# A caller that already has fd 9 open (a wrapper script) must not switch the
# lock off: ctmux takes its own fd 9.
test_inherited_fd9_does_not_disable_the_lock() {
	write_cfg "$PASSWORDLESS"
	python3 - "$W/home/$HEAL_LOCK_REL" "$W/holder_ready" "$W/holder_done" <<'PY' &
import fcntl, sys, time
f = open(sys.argv[1], "a")
fcntl.flock(f, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(2)
open(sys.argv[3], "w").close()
PY
	wait_for_file "$W/holder_ready" 10 || fail "holder never took the lock" || return 1
	HOME="$W/home" PATH="$TEST_PATH" CMUX_SOCKET_PATH="$W/cmux.sock" \
		env -u CMUX_SOCKET_PASSWORD -u CMUX_SOCKET_CAPABILITY \
		"$CTMUX" ensure 9>/dev/null >"$W/out" 2>"$W/err"
	RC=$?
	local done_before_exit=""
	[ -e "$W/holder_done" ] && done_before_exit=1
	wait
	assert_healed || return 1
	[ -n "$done_before_exit" ] || fail "ctmux healed while the lock was held (inherited fd 9 disabled it)"
}

# The lock really serializes: a run that starts while another process holds
# it does not finish before the holder lets go.
test_live_lock_holder_is_waited_for() {
	write_cfg "$PASSWORDLESS"
	python3 - "$W/home/$HEAL_LOCK_REL" "$W/holder_ready" "$W/holder_done" <<'PY' &
import fcntl, sys, time
f = open(sys.argv[1], "a")
fcntl.flock(f, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(2)
open(sys.argv[3], "w").close()
PY
	wait_for_file "$W/holder_ready" 10 || fail "holder never took the lock" || return 1
	run_ctmux ensure
	local done_before_exit=""
	[ -e "$W/holder_done" ] && done_before_exit=1
	wait
	assert_healed || return 1
	[ -n "$done_before_exit" ] || fail "ctmux finished its heal while the lock was still held"
}

# ---------- #5: panes whose auto-resume failed ----------

# cmux types `cmux restore <kind> <id>` into each restored terminal during the
# very launch that drops the socket password, so the pane is left at a shell
# showing auth_required. After the heal ctmux re-runs it, in that pane only.
test_resume_reruns_a_restore_that_failed_with_auth_required() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ "$(sent)" = 'send --surface surface:1 cmux restore --surface surface:1\n' ] || fail "sent: $(sent)"
}

# The ticket's second criterion: only an idle shell showing that error is ever
# typed into. A pane whose foreground process is a tmux client is left alone
# even when the error text is on its screen (a tmux pane can show anything).
test_resume_never_types_into_a_pane_running_tmux() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 0 tmux main "$(failed_restore_screen tmux main "$AUTH_ERR")"
	run_ctmux resume
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ -z "$(sent)" ] || fail "typed into a busy pane: $(sent)"
}

test_resume_leaves_an_idle_pane_without_the_error_alone() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(printf '> ls\nREADME.md\n\n~ > ')"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed into a healthy pane: $(sent)"
}

test_resume_leaves_a_pane_without_a_binding_alone() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 "" "" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed into a pane with no resume binding: $(sent)"
}

# The user already did something after the failed restore: not ours to redo.
test_resume_ignores_an_error_that_is_no_longer_the_latest_output() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")
> git status
On branch main
nothing to commit
~ > "
	run_ctmux resume
	[ -z "$(sent)" ] || fail "re-ran a stale failure: $(sent)"
}

# Not the command cmux typed for this binding: not a failure of ours to redo.
test_resume_ignores_an_error_from_another_restore_command() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude 99999999-0000-0000-0000-000000000000 "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed for a different checkpoint: $(sent)"
}

# Our own retry failing must not turn into a retry loop on every run.
test_resume_does_not_retry_its_own_failed_retry() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(printf '> cmux restore claude %s\n%s\n> cmux restore --surface surface:1\n%s\n\n~ > ' \
		"$SESSION_ID" "$AUTH_ERR" "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "retried its own failed retry: $(sent)" || return 1
	grep -q 'failed too' "$W/out" || fail "no explanation printed: $(cat "$W/out")"
}

# Resuming a session that runs somewhere else would put a second writer on it.
test_resume_never_starts_a_session_that_is_still_running_elsewhere() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	printf '#!/bin/bash\necho "abduco -f -A omp-1 omp --resume /s/2026_%s.jsonl"\n' "$SESSION_ID" >"$W/bin/ps"
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "resumed a live session: $(sent)"
	grep -q 'still running elsewhere' "$W/out" || fail "no explanation printed: $(cat "$W/out")"
}

# A tmux pane restored before the server was up fails with tmux's own error.
test_resume_reruns_a_tmux_restore_that_ran_before_the_server() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 tmux main "$(failed_restore_screen tmux main 'no server running on /private/tmp/tmux-501/default')"
	run_ctmux resume
	[ "$(sent)" = 'send --surface surface:1 cmux restore --surface surface:1\n' ] || fail "sent: $(sent)"
}

# That error text only counts for a tmux binding.
test_resume_ignores_tmux_errors_for_agent_bindings() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" 'no server running on /private/tmp/tmux-501/default')"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed for an unrelated error: $(sent)"
}

test_resume_only_touches_the_stalled_pane_among_several() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 0 tmux main "$(failed_restore_screen tmux main "$AUTH_ERR")"
	fx_surface surface:2 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	fx_surface surface:3 1 "" "" "$(printf '~ > ')"
	run_ctmux resume
	[ "$(sent)" = 'send --surface surface:2 cmux restore --surface surface:2\n' ] || fail "sent: $(sent)"
}

# Running it again (the login agent, then a manual ctmux, then the watcher)
# must not type the restore a second time into a pane that is already on it.
test_resume_twice_types_once() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	run_ctmux resume
	[ "$(sent | wc -l | tr -d ' ')" = 1 ] || fail "typed $(sent | wc -l | tr -d ' ') times: $(sent)"
}

# Two runs at once: the second waits for the first, then finds the restore
# already typed and leaves the pane alone.
test_overlapping_resumes_type_once() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	touch "$W/gate_send"
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux_bg a resume
	wait_for_file "$W/send_started" 10 || fail "run a never reached send: $(cat "$W/err.a")" || return 1
	run_ctmux_bg b resume
	/bin/sleep 1
	touch "$W/release_send"
	wait
	[ "$(cat "$W/rc.a")" = 0 ] && [ "$(cat "$W/rc.b")" = 0 ] || fail "exit a=$(cat "$W/rc.a") b=$(cat "$W/rc.b")" || return 1
	[ "$(sent | wc -l | tr -d ' ')" = 1 ] || fail "typed $(sent | wc -l | tr -d ' ') times: $(sent)"
}

# One ps snapshot serves the whole scan, so it cannot see the session the scan
# itself just resumed: a second pane bound to the same id must not get it too.
test_resume_never_resumes_one_session_in_two_panes() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	fx_surface surface:2 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	[ "$(sent | wc -l | tr -d ' ')" = 1 ] || fail "typed $(sent | wc -l | tr -d ' ') times: $(sent)" || return 1
	grep -q 'already being resumed' "$W/out" || fail "no explanation printed: $(cat "$W/out")"
}

# Panes that share a tmux binding are each attached to the session on their
# own: that is not a second writer on one agent session.
test_resume_reattaches_every_pane_bound_to_the_same_tmux_session() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 tmux main "$(failed_restore_screen tmux main "$AUTH_ERR")"
	fx_surface surface:2 1 tmux main "$(failed_restore_screen tmux main "$AUTH_ERR")"
	run_ctmux resume
	[ "$(sent | wc -l | tr -d ' ')" = 2 ] || fail "typed $(sent | wc -l | tr -d ' ') times: $(sent)"
}

# When the typed command is slow to appear (or never does) the pane still shows
# the original failure; a later run must not type the restore on top of it.
test_resume_does_not_retype_while_the_echo_has_not_appeared() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	touch "$W/no_echo"
	export CTMUX_RESUME_ECHO_WAIT_S=0.5
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	run_ctmux resume
	[ "$(sent | wc -l | tr -d ' ')" = 1 ] || fail "typed $(sent | wc -l | tr -d ' ') times: $(sent)" || return 1
	grep -q 'typed earlier' "$W/out" || fail "no explanation printed: $(cat "$W/out")"
}

# The idle check is a snapshot taken before the scan walks the panes; the pane
# can go busy meanwhile, and typing then would land in whatever it runs.
test_resume_rechecks_the_pane_right_before_typing() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	printf '%s' '{"windows":[{"workspaces":[{"panes":[{"surfaces":[{"kind":"surface","ref":"surface:1","processes":[{"name":"node","pid":7}],"foreground_pgids":[7]}]}]}]}]}' >"$W/top_later.json"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed into a pane that had gone busy: $(sent)" || return 1
	grep -q 'changed while' "$W/out" || fail "no explanation printed: $(cat "$W/out")"
}

# The user already ran something in the pane after the failure: not ours to redo.
test_resume_leaves_a_pane_the_user_has_used_since_alone() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(printf 'Last login: x\n\n> cmux restore claude %s\n%s\n\n~ > true\n~ > ' "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed into a pane the user had used: $(sent)"
}

# A reported skip is the answer; "no pane is waiting" next to it contradicts it.
test_resume_does_not_claim_nothing_is_waiting_after_reporting_a_pane() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	printf '#!/bin/bash\necho "abduco -f -A omp-1 omp --resume /s/2026_%s.jsonl"\n' "$SESSION_ID" >"$W/bin/ps"
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	grep -q 'still running elsewhere' "$W/out" || fail "no explanation printed: $(cat "$W/out")" || return 1
	! grep -q 'no pane is waiting' "$W/out" || fail "contradictory trailer: $(cat "$W/out")"
}

# A pane that went busy before it was typed into resumed nothing, so a sibling
# bound to the same session is still free to be resumed.
test_resume_does_not_block_a_sibling_when_the_first_pane_was_skipped() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	fx_surface surface:2 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	printf '%s' '{"windows":[{"workspaces":[{"panes":[{"surfaces":[{"kind":"surface","ref":"surface:1","processes":[{"name":"node","pid":7}],"foreground_pgids":[7]},{"kind":"surface","ref":"surface:2","processes":[],"foreground_pgids":[]}]}]}]}]}' >"$W/top_later.json"
	run_ctmux resume
	[ "$(sent)" = 'send --surface surface:2 cmux restore --surface surface:2\n' ] || fail "sent: $(sent)"
}

# The resumed agent may not be in ps yet when the next run starts: the session
# stays claimed across runs.
test_resume_remembers_a_claimed_session_across_runs() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	fx_surface surface:2 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume --surface surface:1
	run_ctmux resume
	[ "$(sent | wc -l | tr -d ' ')" = 1 ] || fail "typed $(sent | wc -l | tr -d ' ') times: $(sent)" || return 1
	grep -q 'already being resumed' "$W/out" || fail "no explanation printed: $(cat "$W/out")"
}

# The typed command may never show on the screen. However long ago that was,
# the unchanged screen still looks like the original failure, and typing again
# would repeat a restore that may well have run. A changed screen (a new cmux
# launch prints a new login line) is a new situation.
test_resume_remembers_an_unechoed_restore_until_the_screen_changes() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	touch "$W/no_echo"
	export CTMUX_RESUME_ECHO_WAIT_S=0.3
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	python3 - "$W/home/.config/cmux/.ctmux-resume.sent" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["claims"] = {k: v - 86400 for k, v in d["claims"].items()}
json.dump(d, open(sys.argv[1], "w"))
PY
	run_ctmux resume
	[ "$(sent | wc -l | tr -d ' ')" = 1 ] || fail "typed again over an unchanged screen: $(sent)" || return 1
	printf 'Last login: Fri Oct  2 09:00:00 on ttys002\n\n> cmux restore claude %s\n%s\n\n~ > ' "$SESSION_ID" "$AUTH_ERR" >"$W/screen.surface:1"
	run_ctmux resume
	[ "$(sent | wc -l | tr -d ' ')" = 2 ] || fail "did not handle the new failure: $(sent)"
}

# Text the user has typed after the failure is not an empty prompt.
test_resume_never_types_after_text_the_user_has_started_typing() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(printf 'Last login: x\n\n> cmux restore claude %s\n%s\n~ > git status' "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed into a half-typed command line: $(sent)"
}

# `cmux restore tmux main2` failing says nothing about a binding to `main`.
test_resume_matches_the_binding_id_as_a_whole() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 tmux main "$(failed_restore_screen tmux main2 "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "mistook another restore for this binding's: $(sent)"
}

# Two unlocked scans could both type into one pane, so without the lock
# nothing is typed at all.
test_resume_does_nothing_when_it_cannot_take_its_lock() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	mkdir "$W/home/.config/cmux/.ctmux-resume.lock"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed without the lock: $(sent)" || return 1
	grep -q "couldn't take the resume lock" "$W/err" || fail "no explanation printed: $(cat "$W/err")"
}

test_resume_dry_run_only_reports() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume --dry-run
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ -z "$(sent)" ] || fail "sent in dry-run: $(sent)" || return 1
	grep -q 'surface:1' "$W/out" || fail "dry-run did not name the pane: $(cat "$W/out")"
}

# --surface limits the run to the named panes (a live check on a disposable
# workspace must not touch the user's real panes).
test_resume_surface_option_limits_the_scope() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface surface:1 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	fx_surface surface:2 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume --surface surface:2
	[ "$(sent)" = 'send --surface surface:2 cmux restore --surface surface:2\n' ] || fail "sent: $(sent)"
}

# Only a plain surface ref is ever typed; anything else in the catalog is skipped.
test_resume_skips_a_surface_ref_that_is_not_a_plain_ref() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	fx_surface 'surface:1;touch-pwned' 1 claude "$SESSION_ID" "$(failed_restore_screen claude "$SESSION_ID" "$AUTH_ERR")"
	run_ctmux resume
	[ -z "$(sent)" ] || fail "typed an unvalidated ref: $(sent)"
}

# The whole point: password dropped by the launch -> ctmux heals it, and only
# then re-runs the pane's restore (which would still fail before the heal).
test_ensure_heals_the_password_then_reruns_the_failed_restore() {
	write_cfg "$PASSWORDLESS"
	fx_surface surface:1 1 tmux main "$(failed_restore_screen tmux main "$AUTH_ERR")"
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ "$(sent)" = 'send --surface surface:1 cmux restore --surface surface:1\n' ] || fail "sent: $(sent)" || return 1
	local reload send
	reload=$(grep -n '^cmux reload-config' "$W/calls_full" | head -1 | cut -d: -f1)
	send=$(grep -n '^cmux send' "$W/calls_full" | head -1 | cut -d: -f1)
	[ -n "$reload" ] && [ -n "$send" ] && [ "$reload" -lt "$send" ] || fail "restore re-run before the heal: $(cat "$W/calls_full")"
}

# A catalog cmux can't produce is not an error of a plain ensure.
test_ensure_is_quiet_when_the_surface_catalog_is_unavailable() {
	write_cfg "$CFG_OK"
	app_has_pw cafe
	run_ctmux ensure
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ ! -s "$W/err" ] || fail "stderr not empty: $(cat "$W/err")"
}

# ---------- runner ----------

# bash tests/ctmux_test.sh [test_name...] runs only the named tests.
for t in $(declare -F | awk '{print $3}' | grep '^test_'); do
	if [ "$#" -gt 0 ] && [[ " $* " != *" $t "* ]]; then continue; fi
	new_world
	# Subshell: a test can't leak state into the next. (`set -e` would be
	# ignored in an `if` condition anyway, so every assertion returns 1.)
	if ("$t"); then
		PASS=$((PASS + 1))
		echo "ok   $t"
	else
		FAIL=$((FAIL + 1))
		FAILED_NAMES+=("$t")
		echo "FAIL $t"
	fi
	chmod -R u+w "$W" 2>/dev/null
	rm -rf "$W"
done
echo "passed: $PASS, failed: $FAIL"
[ "$FAIL" -eq 0 ] || { echo "failed: ${FAILED_NAMES[*]}"; exit 1; }
