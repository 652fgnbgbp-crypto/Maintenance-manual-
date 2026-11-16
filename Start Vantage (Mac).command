#!/bin/bash
# Double-click to start Vantage. Keep the window open while you use it; close it to stop Vantage.
cd "$(dirname "$0")"
for py in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    exec "$py" launcher.py "$@"
  fi
done
echo "Vantage needs Python 3.10 or newer. Install it from https://www.python.org/downloads/macos/ and try again."
read -r -p "Press Enter to close."
