#!/bin/bash
HERE="$(cd "$(dirname "$0")" && pwd)"
ENTRY="[Desktop Entry]
Type=Application
Name=Vantage
Comment=Vantage by Torwood Aerospace Group
Exec=bash \"$HERE/start-vantage-linux.sh\"
Icon=$HERE/desktop/vantage.png
Terminal=true
Categories=Utility;"
mkdir -p "$HOME/.local/share/applications"
echo "$ENTRY" > "$HOME/.local/share/applications/vantage.desktop"
if [ -d "$HOME/Desktop" ]; then echo "$ENTRY" > "$HOME/Desktop/vantage.desktop"; chmod +x "$HOME/Desktop/vantage.desktop"; fi
echo "Vantage added to your applications menu (and Desktop, if you have one)."
