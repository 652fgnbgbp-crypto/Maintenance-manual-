#!/bin/bash
# Adds a Vantage icon to the Desktop that starts Vantage and opens it in the browser.
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Desktop/Vantage.app"
xattr -dr com.apple.quarantine "$HERE" 2>/dev/null
chmod +x "$HERE/Start Vantage (Mac).command"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$HERE/desktop/vantage.icns" "$APP/Contents/Resources/vantage.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Vantage</string>
  <key>CFBundleDisplayName</key><string>Vantage</string>
  <key>CFBundleIdentifier</key><string>local.torwood.vantage</string>
  <key>CFBundleExecutable</key><string>vantage</string>
  <key>CFBundleIconFile</key><string>vantage</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.0</string>
</dict></plist>
PLIST
cat > "$APP/Contents/MacOS/vantage" <<SCRIPT
#!/bin/bash
open -a Terminal "$HERE/Start Vantage (Mac).command"
SCRIPT
chmod +x "$APP/Contents/MacOS/vantage"
touch "$APP"
echo "A Vantage icon has been added to your Desktop."
echo "If you move the Vantage folder, run this installer again."
read -r -p "Press Enter to close."
