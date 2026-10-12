import copy
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["HERDR_PLUGIN_STATE_DIR"] = tempfile.mkdtemp()
spec = importlib.util.spec_from_file_location("matrix", os.path.join(ROOT, "matrix.py"))
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


class FakeHerdr:
    def __init__(self):
        self.spaces = {
            "w1": {"workspace_id": "w1", "label": "secret", "number": 1, "focused": True,
                   "active_tab_id": "w1:t1", "tokens": {"numbered": "[1] secret", "wsnum": "[1]", "kanji": "一 secret"}},
            "w2": {"workspace_id": "w2", "label": "other", "number": 2, "focused": False,
                   "active_tab_id": "w2:t1", "tokens": {"numbered": "[2] other", "wsnum": "[2]", "kanji": "二 other"}},
        }
        self.panes = [
            {"pane_id": "w1:p1", "workspace_id": "w1", "tab_id": "w1:t1", "label": "a"},
            {"pane_id": "w1:p2", "workspace_id": "w1", "tab_id": "w1:t2", "label": "b"},
            {"pane_id": "w2:p1", "workspace_id": "w2", "tab_id": "w2:t1", "label": "c"},
        ]
        self.calls = []
        self.next_pane = 10

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("workspace", "list"):
            return {"result": {"workspaces": copy.deepcopy(list(self.spaces.values()))}}
        if args[:2] == ("pane", "list"):
            return {"result": {"panes": copy.deepcopy(self.panes)}}
        if args[:2] in (("workspace", "report-metadata"), ("pane", "report-metadata")):
            if args[0] == "workspace":
                tokens = self.spaces[args[2]]["tokens"]
            else:
                tokens = next(p for p in self.panes if p["pane_id"] == args[2]).setdefault("tokens", {})
            rest = args[5:]
            for flag, value in zip(rest[::2], rest[1::2]):
                if flag == "--token":
                    key, val = value.split("=", 1)
                    tokens[key] = val
                elif flag == "--clear-token":
                    tokens.pop(value, None)
        if args[:3] == ("plugin", "pane", "open"):
            target = next(p for p in self.panes if p["pane_id"] == args[args.index("--target-pane") + 1])
            pane = {"pane_id": f"{target['workspace_id']}:p{self.next_pane}", "label": "Rain",
                    "workspace_id": target["workspace_id"], "tab_id": target["tab_id"]}
            self.next_pane += 1
            self.panes.append(pane)
            return {"result": {"plugin_pane": {"pane": pane}}}
        if args[:3] == ("plugin", "pane", "close"):
            self.panes = [p for p in self.panes if p["pane_id"] != args[3]]
        return {}

    def rain(self):
        return [p["pane_id"] for p in self.panes if p["label"] == "Rain"]


class BossModeTest(unittest.TestCase):
    def setUp(self):
        self.herdr = FakeHerdr()
        self.views = []
        patches = [
            mock.patch.object(matrix, "state_dir", tempfile.mkdtemp()),
            mock.patch.object(matrix, "run", self.herdr),
            mock.patch.object(matrix, "api", self.api),
            mock.patch.object(matrix.time, "sleep", lambda s: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        matrix.write_json("boss.json", {"spaces": ["w1"]})

    def api(self, method, params):
        self.views.append((method, params))
        return {"result": {"type": "agent_view"}}

    def test_toggle_round_trip(self):
        matrix.toggle_boss()
        tokens = self.herdr.spaces["w1"]["tokens"]
        self.assertEqual(tokens["numbered"], "[1] space-alpha")
        self.assertEqual(tokens["workspace"], "space-alpha")
        self.assertEqual(self.herdr.spaces["w2"]["tokens"]["numbered"], "[2] other")
        self.assertEqual(len(self.herdr.rain()), 2, "one rain per tab")
        self.assertEqual(self.herdr.calls[-2:], [("workspace", "focus", "w1"), ("tab", "focus", "w1:t1")])
        self.assertEqual(self.views[-1], ("agent.view.set", {"source": "matrix", "filter": {
            "op": "not", "filter": {"op": "in", "field": "workspace_id", "values": ["w1"]}}}))

        matrix.toggle_boss()
        self.assertEqual(tokens, {"numbered": "[1] secret", "wsnum": "[1]", "kanji": "一 secret"})
        self.assertEqual(self.herdr.rain(), [])
        self.assertFalse(matrix.load_boss()["on"])
        self.assertEqual(self.views[-1], ("agent.view.clear", {"source": "matrix"}))

    def test_failure_during_rain_keeps_real_name(self):
        with mock.patch.object(matrix, "start_rain", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                matrix.toggle_boss()
        self.assertTrue(matrix.load_boss()["on"])

        matrix.toggle_boss()
        self.assertEqual(self.herdr.spaces["w1"]["tokens"]["numbered"], "[1] secret")

    def test_numbers_follow_label_and_position(self):
        self.herdr.spaces["w2"].update(label="renamed", number=1, tokens={})
        self.herdr.spaces["w1"]["number"] = 2
        matrix.sync_spaces(rain=False)
        self.assertEqual(self.herdr.spaces["w1"]["tokens"], {"numbered": "[2] secret", "wsnum": "[2]", "kanji": "二 secret"})
        self.assertEqual(self.herdr.spaces["w2"]["tokens"], {"numbered": "[1] renamed", "wsnum": "[1]", "kanji": "一 renamed"})

        writes = len(self.herdr.calls)
        matrix.sync_spaces(rain=False)
        self.assertEqual(len(self.herdr.calls), writes + 3, "only the list calls when nothing changed")

    def test_cover_survives_relabel(self):
        matrix.toggle_boss()
        self.herdr.spaces["w1"]["label"] = "moved"
        matrix.sync_spaces(rain=False)
        self.assertEqual(self.herdr.spaces["w1"]["tokens"]["numbered"], "[1] space-alpha")
        self.assertEqual(self.herdr.spaces["w1"]["tokens"]["kanji"], "一 space-alpha")

        matrix.toggle_boss()
        self.assertEqual(self.herdr.spaces["w1"]["tokens"]["numbered"], "[1] moved")

    def test_kanji_numerals(self):
        cases = {1: "一", 9: "九", 10: "十", 11: "十一", 20: "二十", 35: "三十五", 99: "九十九", 100: "100"}
        for n, want in cases.items():
            self.assertEqual(matrix.kanji(n), want)

    def test_startup_replaces_dead_rain_and_keeps_live_rain(self):
        matrix.write_json("boss.json", {"on": True, "spaces": ["w1"], "rain": {"w1": ["w1:p99"]}})
        matrix.on_startup()
        live = self.herdr.rain()
        self.assertEqual(len(live), 2)
        self.assertEqual(sorted(matrix.load_boss()["rain"]["w1"]), sorted(live))

        matrix.on_startup()
        self.assertEqual(self.herdr.rain(), live)

    def test_mark_restores_active_tab(self):
        matrix.write_json("boss.json", {"on": True, "spaces": []})
        matrix.mark_space()
        self.assertEqual(matrix.load_boss()["spaces"], ["w1"])
        self.assertIn(("tab", "focus", "w1:t1"), self.herdr.calls[-3:])

    def test_idle_rain_ignores_boss_rain(self):
        matrix.toggle_boss()
        self.assertFalse(matrix.rain_open())

    def test_boss_with_no_marked_spaces_clears_view(self):
        matrix.write_json("boss.json", {"spaces": []})
        matrix.toggle_boss()
        self.assertTrue(matrix.load_boss()["on"])
        self.assertEqual(self.views[-1], ("agent.view.clear", {"source": "matrix"}))

    def test_upgrade_drops_hidden_agents_once(self):
        self.herdr.panes[0]["tokens"] = {"hidden": "1", "keep": "x"}
        matrix.drop_hidden_agents()
        self.assertEqual(self.herdr.panes[0]["tokens"], {"keep": "x"})
        self.assertEqual(self.views, [("agent.view.clear", {"source": "matrix"})])

        matrix.drop_hidden_agents()
        self.assertEqual(len(self.views), 1)

    def test_upgrade_retries_when_herdr_is_unreachable(self):
        with mock.patch.object(matrix, "api", return_value={}):
            matrix.drop_hidden_agents()
        matrix.drop_hidden_agents()
        self.assertEqual(self.views, [("agent.view.clear", {"source": "matrix"})])

    def test_cover_names_number_each_lap(self):
        self.assertEqual(matrix.cover_name(0), "space-alpha")
        self.assertEqual(matrix.cover_name(23), "space-omega")
        self.assertEqual(matrix.cover_name(24), "space-alpha-2")

    def test_open_in_zed_uses_active_tab_cwd(self):
        self.herdr.panes[0]["foreground_cwd"] = "/other"
        self.herdr.panes[1]["foreground_cwd"] = "/proj"
        self.herdr.spaces["w1"]["active_tab_id"] = "w1:t2"
        with mock.patch.object(matrix.subprocess, "run") as sp:
            matrix.open_in_zed()
        sp.assert_called_once_with(["zed", "-e", "/proj"], timeout=10, check=True)

    def test_open_in_vscode_reuses_window(self):
        self.herdr.panes[0]["foreground_cwd"] = "/proj"
        with mock.patch.object(matrix.subprocess, "run") as sp:
            matrix.open_in_vscode()
        sp.assert_called_once_with(["code", "-r", "/proj"], timeout=10, check=True)

    def test_diffstat_formats_shortstat(self):
        out = " 3 files changed, 93 insertions(+), 34 deletions(-)\n"
        with mock.patch.object(matrix.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(matrix.diffstat("/p"), ("+93", "-34"))
        out = " 1 file changed, 2 insertions(+)\n"
        with mock.patch.object(matrix.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(matrix.diffstat("/p"), ("+2", "-0"))
        with mock.patch.object(matrix.subprocess, "run", return_value=mock.Mock(stdout="")):
            self.assertIsNone(matrix.diffstat("/p"))

    def test_label_spaces_sets_and_clears_diffstat(self):
        self.herdr.panes[0]["cwd"] = "/p"
        boss = {"on": False, "spaces": [], "rain": {}}
        matrix.label_spaces(boss, {"w1": ("+1", "-2")})
        tokens = self.herdr.spaces["w1"]["tokens"]
        self.assertEqual((tokens["diffstat"], tokens["diffadd"], tokens["diffdel"]), ("+1 -2", "+1", "-2"))
        matrix.label_spaces(boss)
        self.assertEqual(tokens["diffadd"], "+1", "unknown diffs leave tokens alone")
        matrix.label_spaces(boss, {"w1": None})
        self.assertFalse({"diffstat", "diffadd", "diffdel"} & set(tokens))

    def test_diffstat_off_in_plugin_config(self):
        self.herdr.panes[0]["cwd"] = "/p"
        cfg = tempfile.mkdtemp()
        with open(os.path.join(cfg, "config.toml"), "w") as f:
            f.write("diffstat = false\n")
        boss = {"on": False, "spaces": [], "rain": {}}
        with mock.patch.object(matrix, "config_dir", cfg), mock.patch.object(matrix, "diffstat") as d:
            self.assertEqual(matrix.collect_diffs(), {})
        d.assert_not_called()

    def test_collect_diffs_maps_space_to_stat(self):
        self.herdr.panes[0]["cwd"] = "/p"
        with mock.patch.object(matrix, "diffstat", return_value=("+1", "-2")) as d:
            self.assertEqual(matrix.collect_diffs(), {"w1": ("+1", "-2"), "w2": None})
        d.assert_called_once_with("/p")

    def test_client_attached_reads_title_reply(self):
        replies = {"cleared": True, "no_foreground_client": False}
        for reason, want in replies.items():
            reply = {"result": {"type": "client_window_title", "reason": reason}}
            with mock.patch.object(matrix, "api", return_value=reply):
                self.assertIs(matrix.client_attached(), want)
        with mock.patch.object(matrix, "api", return_value={}):
            self.assertIsNone(matrix.client_attached())

    def test_stations_from_config_and_saved_choice(self):
        cfg = tempfile.mkdtemp()
        with open(os.path.join(cfg, "config.toml"), "w") as f:
            f.write('[[stations]]\nname = "One"\nurl = "http://one"\n\n'
                    '[[stations]]\nurl = "http://two"\n\n[[stations]]\nname = "no url"\n')
        with mock.patch.object(matrix, "config_dir", cfg):
            self.assertEqual(matrix.stations(), [{"name": "One", "url": "http://one"},
                                                 {"name": "http://two", "url": "http://two"}])
            matrix.write_json("station.json", "http://two")
            self.assertEqual(matrix.current_station()["url"], "http://two")
            matrix.write_json("station.json", "http://removed")
            self.assertEqual(matrix.current_station()["url"], "http://one")
        matrix.write_json("station.json", "")
        self.assertEqual(matrix.stations(), matrix.STATIONS)

    def test_music_watch_follows_client_and_exits_with_server(self):
        player = mock.Mock()
        player.poll.return_value = None
        seen = iter([True, None, True, False, None, None, None])
        with mock.patch.object(matrix, "client_attached", side_effect=lambda: next(seen)), \
                mock.patch.object(matrix.subprocess, "Popen", return_value=player) as popen, \
                mock.patch.object(matrix.time, "sleep"):
            matrix.music_watch()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][-1], matrix.STATIONS[0]["url"])
        self.assertEqual(player.terminate.call_count, 2, "stops on detach and on exit, not on the slow reply")

    def test_picker_keys(self):
        cases = [
            (b"\x1b[A", 0, (2, None)), (b"k", 1, (0, None)),
            (b"\x1b[B", 2, (0, None)), (b"\x1bOB", 0, (1, None)), (b"j", 0, (1, None)),
            (b"\r", 1, (1, 1)), (b"3", 0, (0, 2)), (b"4", 0, (0, None)), (b"0", 0, (0, None)),
            (b"\x1b", 1, (1, -1)), (b"q", 1, (1, -1)), (b"x", 1, (1, None)),
        ]
        for key, cursor, want in cases:
            self.assertEqual(matrix.picker_key(key, cursor, 3), want, key)

    def test_read_key_joins_split_arrow(self):
        r, w = os.pipe()
        os.write(w, b"\x1b[Aj")
        self.assertEqual(matrix.read_key(r), b"\x1b[A")
        self.assertEqual(matrix.read_key(r), b"j")
        os.write(w, b"\x1b")
        self.assertEqual(matrix.read_key(r), b"\x1b")
        os.close(r)
        os.close(w)

    def test_toggle_music_starts_then_stops(self):
        with mock.patch.object(matrix, "ours", return_value=False), \
                mock.patch.object(matrix, "music_group_alive", return_value=False), \
                mock.patch.object(matrix.shutil, "which", return_value="/bin/ffplay"), \
                mock.patch.object(matrix.subprocess, "Popen", return_value=mock.Mock(pid=4242)) as popen:
            matrix.toggle_music()
        self.assertIn("music-watch", popen.call_args.args[0])
        self.assertEqual(matrix.read_json(matrix.music_file(), {}),
                         {"pid": 4242, "station": matrix.STATIONS[0]["name"]})
        with mock.patch.object(matrix, "ours", return_value=True), \
                mock.patch.object(matrix, "music_group_alive", return_value=True), \
                mock.patch.object(matrix.os, "killpg") as killpg:
            matrix.toggle_music()
        killpg.assert_called_once_with(4242, matrix.signal.SIGTERM)
        self.assertEqual(matrix.read_json(matrix.music_file(), 0), {})
        self.assertIn(("notification", "show", "Matrix: music off"), self.herdr.calls)

    def test_stop_music_spares_a_reused_pid(self):
        matrix.write_json(matrix.music_file(), {"pid": 4242, "station": "x"})
        with mock.patch.object(matrix, "music_group_alive", return_value=False), \
                mock.patch.object(matrix.os, "killpg") as killpg:
            matrix.stop_music()
        killpg.assert_not_called()

    def test_music_file_differs_per_session(self):
        files = set()
        for sock in ("/a/herdr.sock", "/b/herdr.sock"):
            with mock.patch.dict(os.environ, {"HERDR_SOCKET_PATH": sock}):
                files.add(matrix.music_file())
        self.assertEqual(len(files), 2)

if __name__ == "__main__":
    unittest.main()
