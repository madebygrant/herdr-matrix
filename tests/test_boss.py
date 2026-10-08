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
            "w1": {"workspace_id": "w1", "label": "secret", "focused": True, "active_tab_id": "w1:t1",
                   "tokens": {"numbered": "[1] secret", "wsnum": "[1]"}},
            "w2": {"workspace_id": "w2", "label": "other", "focused": False, "active_tab_id": "w2:t1",
                   "tokens": {"numbered": "[2] other", "wsnum": "[2]"}},
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
        self.assertEqual(tokens, {"numbered": "[1] secret", "wsnum": "[1]"})
        self.assertEqual(self.herdr.rain(), [])
        self.assertFalse(matrix.load_boss()["on"])
        self.assertEqual(self.views[-1], ("agent.view.clear", {"source": "matrix"}))

    def test_failure_during_rain_keeps_real_name(self):
        with mock.patch.object(matrix, "start_rain", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                matrix.toggle_boss()
        boss = matrix.load_boss()
        self.assertTrue(boss["on"])
        self.assertEqual(boss["saved"], {"w1": "[1] secret"})

        matrix.toggle_boss()
        self.assertEqual(self.herdr.spaces["w1"]["tokens"]["numbered"], "[1] secret")

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


if __name__ == "__main__":
    unittest.main()
