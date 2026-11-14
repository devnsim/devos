#!/usr/bin/env bash
# One-time setup: adds DevOS to the application menu and the desktop.
# Re-run it if you ever move the project folder.



set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
APPS="$HOME/.local/share/applications"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

chmod +x "$DIR/run.sh"
mkdir -p "$APPS" "$DESKTOP_DIR"

cat > "$APPS/devos.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DevOS
Comment=DevOS control dashboard
Exec="$DIR/run.sh"
Icon=$DIR/assets/devos.svg
Terminal=false
Categories=Development;Utility;
StartupWMClass=devos
EOF

chmod +x "$APPS/devos.desktop"
cp "$APPS/devos.desktop" "$DESKTOP_DIR/devos.desktop"
chmod +x "$DESKTOP_DIR/devos.desktop"

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true

echo "Installed:"
echo "  App menu: $APPS/devos.desktop"
echo "  Desktop:  $DESKTOP_DIR/devos.desktop"
echo "Errors from icon launches are logged to ~/.cache/devos.log"
