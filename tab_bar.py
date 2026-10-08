import json
import os
import subprocess

# Runs from config.toml, not as a plugin hook, so Herdr doesn't pass the state dir.
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser(
    "~/.local/state/herdr/plugins/madebygrant.herdr-matrix")


def boss_spaces():
    try:
        with open(os.path.join(STATE_DIR, "boss.json")) as f:
            boss = json.load(f)
    except (OSError, ValueError):
        return set()
    return set(boss.get("spaces", [])) if boss.get("on") else set()


try:
    out = subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), "agent", "list"],
                         capture_output=True, text=True, timeout=3).stdout
    hidden = boss_spaces()
    n = sum(a.get("workspace_id") not in hidden for a in json.loads(out)["result"]["agents"])
    print(f"{n} agent{'' if n == 1 else 's'} in the Matrix")
except (subprocess.TimeoutExpired, ValueError, KeyError):
    print("the Matrix")
