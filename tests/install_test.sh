#!/usr/bin/env bash
# tests/install_test.sh — install.sh against a fake world: a throwaway HOME and
# a stub `launchctl` that only records its calls and which labels are loaded.
# The real launchd, ~/Library/LaunchAgents and ~/.local/bin are never touched.
#
# Run: bash tests/install_test.sh [test_name...]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0
FAILED_NAMES=()

fail() {
	echo "    FAIL: $*"
	return 1
}

new_world() {
	W="$(mktemp -d "${TMPDIR:-/tmp}/ctmux-install-test.XXXXXX")"
	mkdir -p "$W/bin" "$W/home"
	: >"$W/lcalls"
	cat >"$W/bin/launchctl" <<'SH'
#!/bin/bash
echo "launchctl $*" >>"$W/lcalls"
case "$1" in
	bootstrap) mkdir -p "$W/loaded"; : >"$W/loaded/$(basename "$3" .plist)" ;;
	bootout) rm -f "$W/loaded/$(basename "${@: -1}" .plist)" ;;
	print) [ -e "$W/loaded/${2##*/}" ]; exit $? ;;
esac
exit 0
SH
	chmod +x "$W/bin/launchctl"
	export W
	AGENTS="$W/home/Library/LaunchAgents"
}

# $1 = on|off
run_install() {
	HOME="$W/home" PATH="$W/bin:$PATH" TMPDIR="$W" CTMUX_LAUNCHAGENTS="$1" \
		bash "$ROOT/install.sh" >"$W/out" 2>"$W/err"
	RC=$?
}

lcalls() { cat "$W/lcalls"; }
count_calls() { lcalls | grep -c "$1"; }
plist_get() { python3 -c 'import plistlib, sys; d = plistlib.load(open(sys.argv[1], "rb")); print(eval(sys.argv[2], {}, {"d": d}))' "$1" "$2"; }

ENSURE=com.ultra.ctmux-ensure
WATCH=com.ultra.ctmux-watch

test_install_renders_and_loads_both_agents() {
	run_install on
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	local label
	for label in $ENSURE $WATCH; do
		plutil -lint "$AGENTS/$label.plist" >/dev/null || fail "$label plist does not lint" || return 1
		[ -e "$W/loaded/$label" ] || fail "$label not bootstrapped" || return 1
		! grep -q '/Users/ultra' "$AGENTS/$label.plist" || fail "$label plist still has the template's home dir" || return 1
	done
	[ "$(plist_get "$AGENTS/$ENSURE.plist" 'd["ProgramArguments"]')" = "['$W/home/.local/bin/ctmux', 'ensure', '--retry-for', '600']" ] ||
		fail "ensure args: $(plist_get "$AGENTS/$ENSURE.plist" 'd["ProgramArguments"]')" || return 1
	[ "$(plist_get "$AGENTS/$ENSURE.plist" 'd["RunAtLoad"]')" = True ] || fail "ensure is not RunAtLoad" || return 1
	[ "$(plist_get "$AGENTS/$WATCH.plist" 'd["ProgramArguments"]')" = "['$W/home/.local/bin/ctmux', 'watch']" ] ||
		fail "watch args: $(plist_get "$AGENTS/$WATCH.plist" 'd["ProgramArguments"]')" || return 1
	[ "$(plist_get "$AGENTS/$WATCH.plist" 'd["WatchPaths"]')" = "['$W/home/.config/cmux/cmux.json']" ] ||
		fail "watch paths: $(plist_get "$AGENTS/$WATCH.plist" 'd["WatchPaths"]')"
}

# A watcher that logs into the directory it watches would wake itself up.
test_watch_agent_does_not_log_into_the_watched_directory() {
	run_install on
	local watched logdir
	watched=$(plist_get "$AGENTS/$WATCH.plist" 'd["WatchPaths"][0]')
	logdir=$(dirname "$(plist_get "$AGENTS/$WATCH.plist" 'd["StandardOutPath"]')")
	[ "$logdir" != "$(dirname "$watched")" ] || fail "watch log is in $logdir, next to the watched file" || return 1
	[ -d "$logdir" ] || fail "log directory $logdir missing: launchd would fail to start the agent"
}

test_second_run_changes_nothing() {
	run_install on
	local before after
	before=$(cat "$AGENTS"/*.plist | cksum)
	: >"$W/lcalls"
	run_install on
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	after=$(cat "$AGENTS"/*.plist | cksum)
	[ "$before" = "$after" ] || fail "plists rewritten" || return 1
	! lcalls | grep -qE 'bootstrap|bootout' || fail "launchd touched again: $(lcalls)" || return 1
	grep -q 'already installed' "$W/out" || fail "no 'already installed' line: $(cat "$W/out")"
}

test_a_changed_agent_is_reinstalled_alone() {
	run_install on
	printf '\n<!-- stale -->\n' >>"$AGENTS/$ENSURE.plist"
	: >"$W/lcalls"
	run_install on
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ "$(count_calls "bootstrap .*$ENSURE")" = 1 ] || fail "ensure not reloaded: $(lcalls)" || return 1
	[ "$(count_calls "bootstrap .*$WATCH")" = 0 ] || fail "watch reloaded needlessly: $(lcalls)" || return 1
	! grep -q stale "$AGENTS/$ENSURE.plist" || fail "stale plist kept"
}

# An agent that was unloaded behind install's back is loaded again.
test_an_unloaded_agent_is_loaded_again() {
	run_install on
	rm -f "$W/loaded/$WATCH"
	: >"$W/lcalls"
	run_install on
	[ -e "$W/loaded/$WATCH" ] || fail "watch not loaded again: $(lcalls)"
}

test_disabled_removes_both_agents_and_is_repeatable() {
	run_install on
	run_install off
	[ "$RC" -eq 0 ] || fail "exit $RC: $(cat "$W/err")" || return 1
	[ ! -e "$AGENTS/$ENSURE.plist" ] && [ ! -e "$AGENTS/$WATCH.plist" ] || fail "plists left behind" || return 1
	[ ! -e "$W/loaded/$ENSURE" ] && [ ! -e "$W/loaded/$WATCH" ] || fail "agents still loaded" || return 1
	run_install off
	[ "$RC" -eq 0 ] || fail "second disabled run failed: $(cat "$W/err")"
}

for t in $(declare -F | awk '{print $3}' | grep '^test_'); do
	if [ "$#" -gt 0 ] && [[ " $* " != *" $t "* ]]; then continue; fi
	new_world
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
