import json
import os
import subprocess
import sys

# Runs from config.toml, not as a plugin hook, so Herdr doesn't pass the state dir.
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser(
    "~/.local/state/herdr/plugins/madebygrant.herdr-matrix")
# Same per-session rule as matrix.py's session_state_dir().
_session = os.path.dirname(os.environ.get("HERDR_SOCKET_PATH", ""))
if os.path.basename(os.path.dirname(_session)) == "sessions":
    STATE_DIR = os.path.join(STATE_DIR, "sessions", os.path.basename(_session))


def boss_spaces():
    try:
        with open(os.path.join(STATE_DIR, "boss.json")) as f:
            boss = json.load(f)
    except (OSError, ValueError):
        return set()
    return set(boss.get("spaces", [])) if boss.get("on") else set()


def agent_count():
    try:
        out = subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), "agent", "list"],
                             capture_output=True, text=True, timeout=3).stdout
        hidden = boss_spaces()
        n = sum(a.get("workspace_id") not in hidden for a in json.loads(out)["result"]["agents"])
        print(f"{n} agent{'' if n == 1 else 's'} in the Matrix")
    except (subprocess.TimeoutExpired, ValueError, KeyError):
        print("the Matrix")


def music():
    # Printing nothing hides the entry.
    try:
        with open(os.path.join(STATE_DIR, "music.json")) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return
    pid = state.get("pid") if isinstance(state, dict) else None
    if not pid:
        return
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                         capture_output=True, text=True).stdout
    if "music-watch" in cmd:
        print(f"♪ {state.get('station', '')}")


if sys.argv[1:] == ["music"]:
    music()
else:
    agent_count()
