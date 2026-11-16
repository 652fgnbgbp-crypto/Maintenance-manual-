"""Start Vantage on this computer and open it in the web browser.

Run by the desktop icon (or the "Start Vantage" file for your system). The first run sets up a
private Python environment in .venv and installs what Vantage needs (a few minutes, needs internet).
After that it starts in a few seconds. Close the window it runs in to stop Vantage.

Needs Python 3.10 or newer (https://www.python.org/downloads/).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

HOME = Path(__file__).resolve().parent
VENV = HOME / ".venv"
SETTINGS = HOME / "settings.env"
PORT = int(os.environ.get("VANTAGE_PORT", "8765"))

SETTINGS_TEMPLATE = """# Vantage settings. Edit with any text editor, then restart Vantage.
# Your Anthropic API key (https://platform.claude.com, under API Keys). Needed for diagnosis.
# Keep this file private: the key is stored here in plain text.
ANTHROPIC_API_KEY=

# Aircraft type for this library (can also be changed in the app, on the Organisation page).
MX_AIRCRAFT_TYPE=

# Where the library (database, uploaded manuals, page images) is kept. Blank = the "data" folder here.
MX_DATA_DIR=
"""


def say(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    say("\n" + msg)
    if sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close this window.")
    sys.exit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_environment() -> None:
    marker = VENV / ".vantage-installed"
    req = (HOME / "requirements.txt").read_text()
    if marker.exists() and marker.read_text() == req:
        return
    if not venv_python().exists():
        say("First start: setting up Vantage (this takes a few minutes and needs internet)…")
        venv.EnvBuilder(with_pip=True).create(VENV)
    say("Installing the components Vantage needs…")
    cmd = [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
           str(HOME / "requirements.txt")]
    if subprocess.call(cmd) != 0:
        fail("Installing Vantage's components failed. Check the internet connection and try again.")
    marker.write_text(req)


def load_settings() -> dict:
    if not SETTINGS.exists():
        SETTINGS.write_text(SETTINGS_TEMPLATE)
    values = {}
    for line in SETTINGS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def ask_for_key(values: dict) -> None:
    if values.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        return
    if not (sys.stdin and sys.stdin.isatty()):
        return
    say("\nDiagnosis needs an Anthropic API key (create one at https://platform.claude.com under API Keys).")
    key = input("Paste it here and press Enter, or just press Enter to skip for now: ").strip()
    if key:
        text = SETTINGS.read_text().replace("ANTHROPIC_API_KEY=\n", f"ANTHROPIC_API_KEY={key}\n", 1)
        SETTINGS.write_text(text)
        values["ANTHROPIC_API_KEY"] = key
        say("Saved in settings.env.")


def vantage_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/info", timeout=1.5) as r:
            return "aircraft_type" in json.loads(r.read() or b"{}")
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    if sys.version_info < (3, 10):
        fail("Vantage needs Python 3.10 or newer. Install it from https://www.python.org/downloads/ and try again.")
    no_browser = "--no-browser" in sys.argv

    if vantage_running(PORT):
        say(f"Vantage is already running: opening http://localhost:{PORT}")
        if not no_browser:
            webbrowser.open(f"http://localhost:{PORT}")
        return

    port = PORT
    while not port_free(port):
        port += 1

    ensure_environment()
    values = load_settings()
    ask_for_key(values)
    env = dict(os.environ)
    env.update({k: v for k, v in values.items() if v})
    env.setdefault("MX_DATA_DIR", str(HOME / "data"))

    say(f"\nStarting Vantage at http://localhost:{port}")
    server = subprocess.Popen(
        [str(venv_python()), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=HOME, env=env)
    for _ in range(120):
        if server.poll() is not None:
            fail("Vantage stopped while starting. See the messages above.")
        if vantage_running(port):
            break
        time.sleep(0.5)
    else:
        server.terminate()
        fail("Vantage did not start within a minute.")

    if not no_browser:
        webbrowser.open(f"http://localhost:{port}")
    say("Vantage is running. Keep this window open while you use it; close it to stop Vantage.")
    try:
        server.wait()
    except KeyboardInterrupt:
        server.terminate()


if __name__ == "__main__":
    main()
