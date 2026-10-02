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
case "$1" in
	rpc) echo '{"attached":true,"started":true}' ;;
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
	printf '#!/bin/bash\n[ -f "$W/tmux_down" ] && exit 1\nexit 0\n' >"$W/bin/tmux"
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/defaults"
	# The login retry pause (15 s) is the only one that matters to a test:
	# it advances the fake clock (the socket comes up) and takes a moment of
	# real time so a retry loop is not a busy spin.
	cat >"$W/bin/sleep" <<'SH'
#!/bin/bash
if [ "$1" = 15 ]; then
	[ -f "$W/up_after_retry_pause" ] && rm -f "$W/socket_down" "$W/up_after_retry_pause"
	/bin/sleep 0.2
fi
exit 0
SH
	chmod +x "$W/bin/"*
	export W
}

run_ctmux() {
	HOME="$W/home" PATH="$TEST_PATH" CMUX_SOCKET_PATH="$W/cmux.sock" \
		env -u CMUX_SOCKET_PASSWORD -u CMUX_SOCKET_CAPABILITY \
		"$CTMUX" "$@" >"$W/out" 2>"$W/err"
	RC=$?
}

write_cfg() { printf '%s\n' "$1" >"$CFG"; chmod 600 "$CFG"; }
app_has_pw() { printf '%s' "$1" >"$W/app_pw"; }
calls() { cat "$W/calls"; }
json_get() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {"d": d}))' "$CFG" "$1"; }
file_mode() { stat -L -f '%Lp' "$1"; }
line_count() { awk 'END{print NR}' "$1"; }

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

# ---------- #8: login retry, watcher ----------

test_ensure_retries_a_failed_attempt_until_it_works() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	touch "$W/socket_down" "$W/up_after_retry_pause"
	run_ctmux ensure --retry-for 60
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	calls | grep -qx 'cmux ssh-tmux' || fail "mirror not run after the retry: $(calls)" || return 1
	grep -q 'retrying in 15s' "$W/err" || fail "no retry line: $(cat "$W/err")"
}

test_ensure_retry_gives_up_when_the_budget_is_spent() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	touch "$W/socket_down"
	SECONDS=0
	run_ctmux ensure --retry-for 1
	[ "$RC" -ne 0 ] || fail "exit 0 although cmux never answered" || return 1
	[ "$SECONDS" -lt 20 ] || fail "kept retrying for ${SECONDS}s"
}

test_ensure_without_retry_fails_after_one_attempt() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	touch "$W/socket_down"
	run_ctmux ensure
	[ "$RC" -ne 0 ] || fail "exit 0 although cmux never answered" || return 1
	! grep -q 'retrying' "$W/err" || fail "retried without --retry-for: $(cat "$W/err")"
}

test_ensure_rejects_a_bad_retry_budget() {
	run_ctmux ensure --retry-for soon
	[ "$RC" -ne 0 ] || fail "exit 0 for a bad budget" || return 1
	grep -q -- '--retry-for needs a number' "$W/err" || fail "stderr: $(cat "$W/err")"
}

# The wait for tmux 'main' must not show up as a bare `tmux` process (see
# tmux_bin). A shell function named tmux stands in for "the bare name".
test_waiting_for_tmux_never_runs_a_bare_tmux() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	# shellcheck disable=SC2329 # only ever called by ctmux, through the exported env
	tmux() { echo "bare tmux $*" >>"$W/bare_tmux"; }
	export -f tmux
	run_ctmux ensure
	unset -f tmux
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ ! -e "$W/bare_tmux" ] || fail "ran a bare tmux: $(cat "$W/bare_tmux")"
}

# cmux.json changes also when the user quits cmux; watching must not bring it back.
test_watch_never_launches_cmux() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	rm -f "$W/cmux_up"
	run_ctmux watch
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	! calls | grep -q '^open' || fail "launched cmux: $(calls)" || return 1
	! calls | grep -q '^cmux' || fail "talked to a cmux that is not running: $(calls)"
}

test_watch_heals_and_mirrors_a_running_cmux() {
	write_cfg "$PASSWORDLESS"
	run_ctmux watch
	assert_healed
}

test_watch_heals_even_when_tmux_is_down_but_does_not_mirror() {
	write_cfg "$PASSWORDLESS"
	touch "$W/tmux_down"
	run_ctmux watch
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[[ "$(json_get 'd["automation"]["socketPassword"]')" =~ ^[0-9a-f]{48}$ ]] || fail "password not healed" || return 1
	! calls | grep -qx 'cmux ssh-tmux' || fail "mirrored a tmux that is down"
}

# A running cmux that never opens its socket is a failure of the watcher, not a hang.
test_watch_reports_a_socket_that_never_comes_up() {
	write_cfg '{"automation":{"socketControlMode":"password","socketPassword":"cafe"}}'
	app_has_pw cafe
	touch "$W/socket_down"
	run_ctmux watch
	assert_one_actionable_line 'not reachable'
}

# ---------- runner ----------

for t in $(declare -F | awk '{print $3}' | grep '^test_'); do
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
