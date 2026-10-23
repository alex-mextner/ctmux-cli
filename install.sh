#!/usr/bin/env bash
# install.sh — launcher for install.py, the real installer (stdlib Python).
#
# Usage: ./install.sh [--check] [--dry-run]   (see: ./install.sh --help)
# From a checkout it runs the install.py next to it. Piped from curl (no
# checkout) it clones/updates ~/.local/share/ctmux-cli, then runs that one.
set -euo pipefail

URL="https://github.com/alex-mextner/ctmux-cli.git"
die() { echo "install.sh: $*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || die "python3 not found on PATH"

here=""
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
	here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [[ -z "$here" || ! -f "$here/install.py" ]]; then
	for arg in "$@"; do
		case "$arg" in --check | --dry-run) die "no ctmux checkout next to install.sh; run it from a clone of $URL" ;; esac
	done
	here="${XDG_DATA_HOME:-$HOME/.local/share}/ctmux-cli"
	if [[ -d "$here/.git" ]]; then
		[[ "$(git -C "$here" remote get-url origin 2>/dev/null || true)" == "$URL" ]] || die "$here exists but its origin is not $URL; remove it or fix its remote, then re-run"
		git -C "$here" pull --ff-only
	else
		mkdir -p "$(dirname "$here")"
		git clone "$URL" "$here"
	fi
fi
exec python3 "$here/install.py" "$@"
