import json
import os
import subprocess

try:
    out = subprocess.run([os.environ.get("HERDR_BIN_PATH", "herdr"), "agent", "list"],
                         capture_output=True, text=True, timeout=3).stdout
    n = len(json.loads(out)["result"]["agents"])
    print(f"{n} agent{'' if n == 1 else 's'} in the Matrix")
except (subprocess.TimeoutExpired, ValueError, KeyError):
    print("the Matrix")
