#!/usr/bin/env bash
# install.sh — install the `ctmux` wrapper (tmux -CC control mode over cmux,
# looped back to the local tmux server). Symlinks bin/ctmux onto PATH, and
# bootstraps the two LaunchAgents (login: `ctmux ensure`; cmux.json changes:
# `ctmux watch`) ONLY when this machine opts in via
# tools.items.ctmux.enabled: true in ~/.config/rig/config.yaml (default off,
# a standard tools.items.<name> key). CTMUX_LAUNCHAGENTS=on|off overrides that
# (tests, one-off installs). Idempotent: an agent whose rendered plist is
# unchanged and already loaded is left alone, so re-running changes nothing.
#
# Works both from a local clone (./install.sh) and piped from curl.
set -euo pipefail

REPO="ctmux-cli"
GITHUB_USER="ultra"
ENTRY="bin/ctmux"
CLONE_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
RIG_CFG="$HOME/.config/rig/config.yaml"
LAUNCH_AGENT_LABELS=(com.ultra.ctmux-ensure com.ultra.ctmux-watch)
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ctmux"

_script_dir=""
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
	_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "$_script_dir" && -f "$_script_dir/$ENTRY" ]]; then
	SRC="$_script_dir"
	echo "ctmux: using local clone at $SRC"
else
	mkdir -p "$CLONE_BASE"
	CLONE_DIR="$CLONE_BASE/$REPO"
	EXPECT_URL="https://github.com/$GITHUB_USER/$REPO.git"
	if [[ -d "$CLONE_DIR/.git" ]]; then
		actual_url="$(git -C "$CLONE_DIR" remote get-url origin 2>/dev/null || echo "")"
		if [[ "$actual_url" != "$EXPECT_URL" ]]; then
			echo "ERROR: $CLONE_DIR exists but its origin is '$actual_url', not $EXPECT_URL." >&2
			echo "       Remove that directory or fix its remote, then re-run." >&2
			exit 1
		fi
		echo "ctmux: updating existing clone at $CLONE_DIR"
		git -C "$CLONE_DIR" pull --ff-only
	else
		echo "ctmux: cloning $EXPECT_URL into $CLONE_DIR"
		git clone "$EXPECT_URL" "$CLONE_DIR"
	fi
	SRC="$CLONE_DIR"
fi

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
chmod +x "$SRC/bin/ctmux"
ln -sf "$SRC/bin/ctmux" "$BIN/ctmux"
echo "ctmux: symlinked $BIN/ctmux -> $SRC/bin/ctmux"

if [[ ":$PATH:" != *":$BIN:"* ]]; then
	echo ""
	echo "  NOTE: $BIN is not on your PATH."
	echo "  Add the following line to your ~/.zshrc and restart your shell:"
	echo "    export PATH=\"$BIN:\$PATH\""
	echo ""
fi

# Off by default: read tools.items.ctmux.enabled from rig's own config.yaml —
# the supported per-item knob (riglib/config.py _TOOLS_ITEM_KEYS = {enabled,
# repo, bin_dir, brew}), not an invented key. rig's tmux block is
# closed against unknown keys (_reject_unknown_keys), so this must live
# under tools.items, never under tmux.
ctmux_enabled="false"
if [[ "${CTMUX_LAUNCHAGENTS:-}" == on ]]; then
	ctmux_enabled="true"
elif [[ "${CTMUX_LAUNCHAGENTS:-}" == off ]]; then
	ctmux_enabled="false"
elif [[ -f "$RIG_CFG" ]] && command -v python3 >/dev/null 2>&1; then
	ctmux_enabled="$(python3 - "$RIG_CFG" <<'PY' 2>/dev/null || echo false
import sys
try:
	import yaml
	data = yaml.safe_load(open(sys.argv[1]))
	item = data.get("tools", {}).get("items", {}).get("ctmux", {}) or {}
	print(str(bool(item.get("enabled"))).lower())
except Exception:
	print("false")
PY
)"
fi

# The templates carry this author's paths; render them for this machine.
render_agent() {
	sed -e "s#/Users/ultra/.local/bin/ctmux#$BIN/ctmux#g" \
		-e "s#/Users/ultra/.config/cmux#$HOME/.config/cmux#g" \
		-e "s#/Users/ultra/.local/state/ctmux#$STATE_DIR#g" \
		"$SRC/launchd/$1.plist"
}

install_agent() {
	local label="$1" dest="$LAUNCH_AGENTS_DIR/$1.plist" tmp
	tmp="$(mktemp "${TMPDIR:-/tmp}/ctmux-agent.XXXXXX")"
	render_agent "$label" >"$tmp"
	if ! plutil -lint "$tmp" >/dev/null; then
		rm -f "$tmp"
		echo "ERROR: the rendered $label plist does not lint." >&2
		return 1
	fi
	if cmp -s "$tmp" "$dest" && launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
		rm -f "$tmp"
		echo "ctmux: $label already installed and current."
		return 0
	fi
	launchctl bootout "gui/$(id -u)" "$dest" 2>/dev/null || true
	mv "$tmp" "$dest"
	launchctl bootstrap "gui/$(id -u)" "$dest"
	echo "ctmux: $label installed and running."
}

remove_agent() {
	local dest="$LAUNCH_AGENTS_DIR/$1.plist"
	launchctl bootout "gui/$(id -u)" "$dest" 2>/dev/null || true
	rm -f "$dest"
}

mkdir -p "$LAUNCH_AGENTS_DIR"
if [[ "$ctmux_enabled" == "true" ]]; then
	mkdir -p "$HOME/.config/cmux" "$STATE_DIR"
	for label in "${LAUNCH_AGENT_LABELS[@]}"; do
		install_agent "$label"
	done
	echo "ctmux: LaunchAgents enabled (tools.items.ctmux.enabled in $RIG_CFG, or CTMUX_LAUNCHAGENTS=on)."
else
	for label in "${LAUNCH_AGENT_LABELS[@]}"; do
		remove_agent "$label"
	done
	echo "ctmux: tools.items.ctmux.enabled is not true in $RIG_CFG — LaunchAgents not installed."
	echo "       Enable it per-machine by adding to $RIG_CFG:"
	echo "         tools:"
	echo "           items:"
	echo "             ctmux:"
	echo "               repo: ~/xp/ctmux-cli"
	echo "               enabled: true"
	echo "       then re-run this install.sh."
fi

echo ""
echo "  ctmux is installed."
echo "  ctmux            ensure the mirror is up, launch/focus cmux"
echo "  ctmux ls         fzf picker over session:window pairs"
echo "  ctmux new [name] create a new tmux session and mirror it"
echo "  Docs: $SRC/README.md"
echo ""
