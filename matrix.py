import contextlib
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time

# Canon Agents first, then generic surnames in the same vein.
CANON = ["smith", "brown", "jones", "johnson", "jackson", "thompson", "gray"]
SURNAMES = [
    "williams", "white", "adams", "cook", "hill", "miller", "davis", "wilson",
    "moore", "taylor", "anderson", "thomas", "harris", "martin", "clark",
    "lewis", "walker", "hall", "allen", "young", "king", "wright", "scott",
    "green", "baker", "nelson", "carter", "mitchell", "turner", "phillips",
]
LABELS = {
    "working": "jacked in",
    "idle": "standing by",
    "blocked": "awaiting operator",
    "done": "exited",
}
IDLE_SECONDS = 600

herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
state_dir = os.environ.get("HERDR_PLUGIN_STATE_DIR", ".")
os.makedirs(state_dir, exist_ok=True)


def run(*args):
    out = subprocess.run([herdr, *args], capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {}


def agents():
    # None means the list is unknown, so callers must not treat it as "no agents".
    return run("agent", "list").get("result", {}).get("agents")


def api(method, params):
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(os.environ["HERDR_SOCKET_PATH"])
    sock.sendall((json.dumps({"id": "matrix", "method": method, "params": params}) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    return json.loads(data) if data.strip() else {}


def state_path(name):
    return os.path.join(state_dir, name)


def read_json(name, default):
    try:
        with open(state_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, value):
    tmp = state_path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(value, f)
    os.replace(tmp, state_path(name))


@contextlib.contextmanager
def locked(name):
    with open(state_path(name), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def event():
    return json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))


def find_pane_id(item):
    if isinstance(item, dict):
        if isinstance(item.get("pane_id"), str):
            return item["pane_id"]
        for v in item.values():
            found = find_pane_id(v)
            if found:
                return found
    elif isinstance(item, list):
        for v in item:
            found = find_pane_id(v)
            if found:
                return found


def candidates():
    yield from CANON
    yield from SURNAMES
    n = 2
    while True:
        yield f"smith-{n}"
        n += 1


def name_agent(pane):
    # Serialise so two panes detected together can't pick the same name.
    with locked("lock"):
        current = agents()
        me = next((a for a in current or [] if a["pane_id"] == pane), None)
        if me is None:
            return
        if not me.get("name"):
            taken = {a["name"] for a in current if a.get("name")}
            name = next(c for c in candidates() if c not in taken)
            run("agent", "rename", pane, name)
    labels = [x for k, v in LABELS.items() for x in ("--state-label", f"{k}={v}")]
    run("pane", "report-metadata", pane, "--source", "matrix", *labels)
    remember_names()


def remember_names():
    current = agents()
    if current is None:
        return
    # Merge only: removal belongs to reap(), which has to see the dead pane to announce it.
    with locked("names.lock"):
        names = read_json("names.json", {})
        names.update({a["pane_id"]: a["name"] for a in current if a.get("name")})
        write_json("names.json", names)


def next_gen():
    gen = read_json("idle_gen.json", 0) + 1
    write_json("idle_gen.json", gen)
    return gen


def all_quiet(current):
    return bool(current) and all(a["agent_status"] == "idle" for a in current)


def cancel_waiter():
    pid = read_json("waiter.json", 0)
    if not pid:
        return
    # Confirm the pid is still our waiter before signalling; pids get reused.
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                         capture_output=True, text=True).stdout
    if "wait-idle" in cmd:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    write_json("waiter.json", 0)


def on_status():
    remember_names()
    gen = next_gen()
    cancel_waiter()
    if all_quiet(agents()):
        proc = subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), "wait-idle", str(gen)],
            env=os.environ,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        write_json("waiter.json", proc.pid)


def rain_open():
    panes = run("pane", "list").get("result", {}).get("panes", [])
    return any(p.get("label") == "Rain" for p in panes)


def wait_idle(gen):
    time.sleep(IDLE_SECONDS)
    # Any status change since we started bumped the counter and cancels this run.
    if read_json("idle_gen.json", 0) != gen or not all_quiet(agents()) or rain_open():
        return
    run("plugin", "pane", "open", "--plugin", os.environ["HERDR_PLUGIN_ID"],
        "--entrypoint", "rain", "--placement", "overlay", "--focus")


HIDE_FILTER = {"op": "not", "filter": {"op": "eq", "field": {"token": "hidden"}, "value": "1"}}


def toggle_hide(pane):
    info = run("pane", "get", pane).get("result", {}).get("pane", {})
    name = next((a.get("name") for a in agents() or [] if a["pane_id"] == pane), None)
    if not name:
        return run("notification", "show", "Matrix: focus an agent first")
    if (info.get("tokens") or {}).get("hidden") == "1":
        run("pane", "report-metadata", pane, "--source", "matrix", "--clear-token", "hidden")
        run("notification", "show", f"Agent {name} is back in the Matrix")
    else:
        run("pane", "report-metadata", pane, "--source", "matrix", "--token", "hidden=1")
        api("agent.view.set", {"source": "matrix", "filter": HIDE_FILTER})
        run("notification", "show", f"Agent {name} is hidden")


def show_all():
    for a in agents() or []:
        run("pane", "report-metadata", a["pane_id"], "--source", "matrix", "--clear-token", "hidden")
    api("agent.view.clear", {"source": "matrix"})
    run("notification", "show", "All agents are visible")


def toast(names):
    if len(names) == 1:
        text = f"Agent {names[0]} has been deleted."
    else:
        text = f"Agents {', '.join(names[:-1])} and {names[-1]} have been deleted."
    # Herdr shows one toast at a time and answers "busy" to the rest.
    for _ in range(4):
        shown = run("notification", "show", text).get("result", {}).get("shown")
        if shown:
            return
        time.sleep(3)


def reap():
    # Closing a tab or workspace may not emit pane.closed, so diff against live agents.
    current = agents()
    if current is None:
        return
    alive = {a["pane_id"] for a in current}
    with locked("names.lock"):
        names = read_json("names.json", {})
        gone = {p: n for p, n in names.items() if p not in alive}
        for pane in gone:
            names.pop(pane)
        write_json("names.json", names)
    if gone:
        toast(list(gone.values()))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "wait-idle":
        return wait_idle(int(sys.argv[2]))
    if len(sys.argv) > 1 and sys.argv[1] == "toggle-hide":
        pane = os.environ.get("HERDR_PANE_ID") or find_pane_id(
            json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON", "{}")))
        return toggle_hide(pane) if pane else None
    if len(sys.argv) > 1 and sys.argv[1] == "show-all":
        return show_all()
    kind = os.environ.get("HERDR_PLUGIN_EVENT", "")
    pane = os.environ.get("HERDR_PANE_ID") or find_pane_id(event())
    if kind == "pane.agent_detected" and pane:
        name_agent(pane)
    elif kind == "pane.agent_status_changed":
        reap()
        on_status()
    elif kind in ("pane.closed", "pane.exited", "tab.closed", "workspace.closed"):
        reap()


main()
