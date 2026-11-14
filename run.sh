#!/usr/bin/env bash
# Launch DevOS inside its Nix environment.
# Works from anywhere: a desktop icon, the app menu, or a terminal.

# Go to the folder this script lives in (the project root), even through symlinks.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

# When launched from an icon there is no terminal, so keep a log for errors.
LOG="$HOME/.cache/devos.log"
mkdir -p "$(dirname "$LOG")"

exec nix-shell --run "python -m app.main" >"$LOG" 2>&1
