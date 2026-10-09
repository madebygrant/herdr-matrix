import contextlib
import fcntl
import json
import os
import re
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
COVERS = [f"space-{g}" for g in (
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lambda", "mu",
    "nu", "xi", "omicron", "pi", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega")]

herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID", "madebygrant.herdr-matrix")
state_dir = os.environ.get("HERDR_PLUGIN_STATE_DIR", ".")
config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR", ".")
DIFF_TOKENS = ("diffstat", "diffadd", "diffdel")
os.makedirs(state_dir, exist_ok=True)


def run(*args):
    # A hung call would otherwise hold boss.lock and stall every later keypress.
    try:
        out = subprocess.run([herdr, *args], capture_output=True, text=True, timeout=10)
        return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        return {}


def agents():
    # None means the list is unknown, so callers must not treat it as "no agents".
    return run("agent", "list").get("result", {}).get("agents")


def api(method, params):
    data = b""
    try:
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(5)
            sock.connect(os.environ["HERDR_SOCKET_PATH"])
            sock.sendall((json.dumps({"id": "matrix", "method": method, "params": params}) + "\n").encode())
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data) if data.strip() else {}
    except (OSError, KeyError, ValueError):
        return {}


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


def panes():
    return run("pane", "list").get("result", {}).get("panes", [])


def rain_open():
    boss_rain = {p for ids in load_boss()["rain"].values() for p in ids}
    return any(p.get("label") == "Rain" and p["pane_id"] not in boss_rain for p in panes())


def wait_idle(gen):
    time.sleep(IDLE_SECONDS)
    # Any status change since we started bumped the counter and cancels this run.
    if read_json("idle_gen.json", 0) != gen or not all_quiet(agents()) or rain_open():
        return
    run("plugin", "pane", "open", "--plugin", PLUGIN_ID,
        "--entrypoint", "rain", "--placement", "overlay", "--focus")


def load_boss():
    return {"on": False, "spaces": [], "rain": {}, **read_json("boss.json", {})}


def apply_view(boss):
    if boss["on"] and boss["spaces"]:
        return api("agent.view.set", {"source": "matrix", "filter": {"op": "not", "filter": {
            "op": "in", "field": "workspace_id", "values": boss["spaces"]}}})
    return api("agent.view.clear", {"source": "matrix"})


def drop_hidden_agents():
    # 0.2 hid agents with a pane token and a view filter. A reinstall runs no startup
    # hook, so a live server keeps both, with no show-all left to undo them.
    if read_json("migrated.json", 0) >= 1:
        return
    with locked("boss.lock"):
        for p in panes():
            if (p.get("tokens") or {}).get("hidden") == "1":
                run("pane", "report-metadata", p["pane_id"], "--source", "matrix", "--clear-token", "hidden")
        if "result" in apply_view(load_boss()):
            write_json("migrated.json", 1)


def workspaces():
    return run("workspace", "list").get("result", {}).get("workspaces")


def cover_name(i):
    lap = i // len(COVERS)
    return COVERS[i % len(COVERS)] + (f"-{lap + 1}" if lap else "")


def kanji(n):
    digits = "〇一二三四五六七八九"
    if not 0 < n < 100:
        return str(n)
    tens, ones = divmod(n, 10)
    return (digits[tens] if tens > 1 else "") + ("十" if tens else "") + (digits[ones] if ones else "")


def space_cwd(space, all_panes):
    mine = [p for p in all_panes if p.get("workspace_id") == space["workspace_id"]]
    # Prefer the active tab's pane, since a space can hold tabs in different directories.
    mine.sort(key=lambda p: p.get("tab_id") != space["active_tab_id"])
    return next((p.get("foreground_cwd") or p.get("cwd") for p in mine if p.get("foreground_cwd") or p.get("cwd")), None)


def diffstat_enabled():
    try:
        import tomllib
        with open(os.path.join(config_dir, "config.toml"), "rb") as f:
            return tomllib.load(f).get("diffstat", True) is not False
    except (OSError, ImportError, ValueError):
        return True


def diffstat(cwd):
    # Untracked files are skipped on purpose; the count is tracked changes against HEAD.
    try:
        out = subprocess.run(["git", "-C", cwd, "diff", "--shortstat", "HEAD"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    added = re.search(r"(\d+) insertion", out)
    removed = re.search(r"(\d+) deletion", out)
    if not (added or removed):
        return None
    return f"+{added.group(1) if added else 0}", f"-{removed.group(1) if removed else 0}"


def label_spaces(boss):
    live = workspaces()
    if live is None:
        return
    ids = {w["workspace_id"] for w in live}
    boss["spaces"] = [s for s in boss["spaces"] if s in ids]
    boss["rain"] = {s: v for s, v in boss["rain"].items() if s in ids}
    covers = {s: cover_name(i) for i, s in enumerate(boss["spaces"])} if boss["on"] else {}
    live_panes = panes()
    show_diff = diffstat_enabled()
    for w in live:
        sid = w["workspace_id"]
        num = f"[{w['number']}]"
        # Herdr's sidebar has no position token, and joins separate tokens with " · ".
        name = covers.get(sid, w["label"])
        want = {"wsnum": num, "numbered": f"{num} {name}", "kanji": f"{kanji(w['number'])} {name}"}
        if sid in covers:
            want["workspace"] = covers[sid]
        have = w.get("tokens") or {}
        cwd = space_cwd(w, live_panes)
        stat = diffstat(cwd) if show_diff and cwd and sid not in covers else None
        if stat:
            want.update(diffadd=stat[0], diffdel=stat[1], diffstat=" ".join(stat))
        args = [x for k, v in want.items() if have.get(k) != v for x in ("--token", f"{k}={v}")]
        if not stat:
            args += [x for k in DIFF_TOKENS if k in have for x in ("--clear-token", k)]
        if args:
            run("workspace", "report-metadata", sid, "--source", "matrix", *args)


def reveal(space):
    run("workspace", "report-metadata", space, "--source", "matrix", "--clear-token", "workspace")


def start_rain(boss, space):
    live = panes()
    if {p["pane_id"] for p in live} & set(boss["rain"].get(space, [])):
        return
    # Herdr opens plugin panes in another space only zoomed over an existing pane, one per tab.
    tabs = {}
    for p in live:
        if p.get("workspace_id") == space and p.get("label") != "Rain":
            tabs.setdefault(p["tab_id"], p["pane_id"])
    ids = []
    for target in tabs.values():
        opened = run("plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", "rain",
                     "--placement", "zoomed", "--target-pane", target,
                     "--env", "MATRIX_BOSS=1", "--no-focus")
        pane_id = opened.get("result", {}).get("plugin_pane", {}).get("pane", {}).get("pane_id")
        if pane_id:
            ids.append(pane_id)
    boss["rain"][space] = ids


def stop_rain(boss, space):
    for pane_id in boss["rain"].pop(space, []):
        run("plugin", "pane", "close", pane_id)


def refocus(before, spaces):
    # Herdr ignores --no-focus for zoomed panes, jumping to each rain's space and tab.
    for w in before:
        if w["workspace_id"] in spaces and not w.get("focused"):
            run("tab", "focus", w["active_tab_id"])
    focused = next((w for w in before if w.get("focused")), None)
    if focused:
        run("workspace", "focus", focused["workspace_id"])
        run("tab", "focus", focused["active_tab_id"])


def cover_all(boss):
    before = workspaces() or []
    label_spaces(boss)
    write_json("boss.json", boss)
    for space in boss["spaces"]:
        start_rain(boss, space)
    refocus(before, boss["spaces"])


def mark_space():
    before = workspaces() or []
    space = next((w for w in before if w.get("focused")), None)
    if not space:
        return run("notification", "show", "Matrix: focus a space first")
    sid = space["workspace_id"]
    with locked("boss.lock"):
        boss = load_boss()
        try:
            if sid in boss["spaces"]:
                boss["spaces"].remove(sid)
                if boss["on"]:
                    reveal(sid)
                    stop_rain(boss, sid)
                text = f"Space {space['label']} is unmarked"
            else:
                boss["spaces"].append(sid)
                text = f"Space {space['label']} is marked for boss mode"
            if boss["on"]:
                label_spaces(boss)
                write_json("boss.json", boss)
                apply_view(boss)
                if sid in boss["spaces"]:
                    start_rain(boss, sid)
                    refocus(before, [sid])
        finally:
            write_json("boss.json", boss)
    run("notification", "show", text)


def toggle_boss():
    # No toast here: one announcing boss mode would give the game away.
    with locked("boss.lock"):
        boss = load_boss()
        boss["on"] = not boss["on"]
        try:
            if boss["on"]:
                cover_all(boss)
            else:
                for space in list(boss["spaces"]):
                    reveal(space)
                    stop_rain(boss, space)
                label_spaces(boss)
            apply_view(boss)
        finally:
            write_json("boss.json", boss)


def open_in_editor(command, name):
    space = next((w for w in workspaces() or [] if w.get("focused")), None)
    cwd = space and space_cwd(space, panes())
    if not cwd:
        return run("notification", "show", "Matrix: no directory found for this space")
    try:
        subprocess.run([*command, cwd], timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        run("notification", "show", f"Matrix: could not open {name}")


def open_in_zed():
    # -e reuses an existing Zed window; without it the CLI opens a new one.
    open_in_editor(["zed", "-e"], "Zed")


def open_in_vscode():
    # -r reuses the last active VS Code window.
    open_in_editor(["code", "-r"], "VS Code")


def sync_spaces(rain):
    with locked("boss.lock"):
        boss = load_boss()
        try:
            if boss["on"] and rain:
                cover_all(boss)
            else:
                label_spaces(boss)
            if boss["on"]:
                apply_view(boss)
        finally:
            write_json("boss.json", boss)


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


def on_startup():
    # boss.json can outlive a server restart while the rain and covers don't.
    sync_spaces(rain=True)


COMMANDS = {
    "mark-space": mark_space,
    "boss-mode": toggle_boss,
    "open-in-zed": open_in_zed,
    "open-in-vscode": open_in_vscode,
    "startup": on_startup,
}


def main():
    drop_hidden_agents()
    args = sys.argv[1:]
    if len(args) > 1 and args[0] == "wait-idle":
        return wait_idle(int(args[1]))
    if args and args[0] in COMMANDS:
        return COMMANDS[args[0]]()
    kind = os.environ.get("HERDR_PLUGIN_EVENT", "")
    pane = os.environ.get("HERDR_PANE_ID") or find_pane_id(event())
    if kind == "pane.agent_detected" and pane:
        name_agent(pane)
    elif kind == "pane.agent_status_changed":
        reap()
        on_status()
    elif kind in ("pane.closed", "pane.exited", "tab.closed", "workspace.closed"):
        reap()
    if kind.startswith("workspace.") or kind == "tab.renamed":
        sync_spaces(rain=False)


if __name__ == "__main__":
    main()
