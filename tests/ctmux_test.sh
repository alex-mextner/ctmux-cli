#!/usr/bin/env bash
# tests/ctmux_test.sh — black-box tests for bin/ctmux against a fake world.
#
# Every external dependency (cmux, pgrep, open, ssh, nc, tmux, defaults,
# sleep) is a stub in a per-test bin dir, and HOME points at a per-test
# fixture dir, so the real ~/.config/cmux and the live cmux app are never
# touched (CMUX_SOCKET_PATH also points into the fixture, in case a real
# cmux binary is ever reached). The fake `cmux` models the socket auth: an
# authenticated command succeeds only when CMUX_SOCKET_PASSWORD equals the
# password the "app" has loaded ($W/app_pw).
#
# Run: bash tests/ctmux_test.sh
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
[ -f "$W/socket_down" ] && { echo "Error: Failed to connect to socket" >&2; exit 1; }
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
	printf '#!/bin/bash\nexit 0\n' >"$W/bin/tmux"
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

write_cfg() { printf '%s\n' "$1" >"$CFG"; chmod 600 "$CFG"; }
app_has_pw() { printf '%s' "$1" >"$W/app_pw"; }
calls() { cat "$W/calls"; }

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
