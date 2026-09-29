#!/usr/bin/env bash
# install.sh — install the `ctmux` wrapper (tmux -CC control mode over cmux,
# looped back to the local tmux server). Symlinks bin/ctmux onto PATH, and
# bootstraps the login LaunchAgent ONLY when this machine opts in via
# tools.items.ctmux.enabled: true in ~/.config/rig/config.yaml (default off,
# a standard tools.items.<name> key).
#
# Works both from a local clone (./install.sh) and piped from curl.
set -euo pipefail

REPO="ctmux-cli"
GITHUB_USER="ultra"
ENTRY="bin/ctmux"
CLONE_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
RIG_CFG="$HOME/.config/rig/config.yaml"
LAUNCH_AGENT_LABEL="com.ultra.ctmux-ensure"
LAUNCH_AGENT_DEST="$HOME/Library/LaunchAgents/$LAUNCH_AGENT_LABEL.plist"

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
if [[ -f "$RIG_CFG" ]] && command -v python3 >/dev/null 2>&1; then
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

mkdir -p "$HOME/Library/LaunchAgents"
if [[ "$ctmux_enabled" == "true" ]]; then
	mkdir -p "$HOME/.config/cmux"
	sed -e "s#/Users/ultra/.local/bin/ctmux#$BIN/ctmux#" \
		-e "s#/Users/ultra/.config/cmux#$HOME/.config/cmux#" \
		"$SRC/launchd/$LAUNCH_AGENT_LABEL.plist" > "$LAUNCH_AGENT_DEST"
	launchctl bootout "gui/$(id -u)" "$LAUNCH_AGENT_DEST" 2>/dev/null || true
	launchctl bootstrap "gui/$(id -u)" "$LAUNCH_AGENT_DEST"
	echo "ctmux: tools.items.ctmux.enabled=true in $RIG_CFG — login LaunchAgent installed and running."
else
	launchctl bootout "gui/$(id -u)" "$LAUNCH_AGENT_DEST" 2>/dev/null || true
	rm -f "$LAUNCH_AGENT_DEST"
	echo "ctmux: tools.items.ctmux.enabled is not true in $RIG_CFG — login LaunchAgent not installed."
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
