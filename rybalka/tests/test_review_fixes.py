import json
import os
import queue
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
import bot
import examples
from test_examples import make_dir
from test_gui_bot import make_bot


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        text = '{"action": "reply", "messages": ["ок"]}'
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


class ReviewFixesTests(unittest.TestCase):
    def setUp(self):
        self.old = (bot.GUI, bot.log, dict(bot.CFG), bot.STYLE, bot.PERSONA, bot.EXAMPLES, bot.emit)
        bot.log = lambda *a, **k: None
        bot.emit = lambda *a, **k: None
        self.bot = make_bot()
        self.bot.cmds = queue.Queue()

    def tearDown(self):
        bot.GUI, bot.log, cfg, bot.STYLE, bot.PERSONA, bot.EXAMPLES, bot.emit = self.old
        bot.CFG.clear()
        bot.CFG.update(cfg)

    def test_safety_rules_are_always_appended_to_the_system_prompt(self):
        self.bot.client = FakeClient()
        self.bot.ask_model("", [{"role": "user", "content": "привет"}])  # владелец стёр легенду целиком
        system = self.bot.client.calls[-1]["system"]
        self.assertTrue(system.endswith(bot.SAFETY_RULES))
        for needle in ("escalate", "18", "бот", "самоповреждени"):
            self.assertIn(needle, bot.SAFETY_RULES)

    def test_reload_config_keeps_runtime_auto_adopt_choice(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "config.json").write_text(json.dumps({"auto_adopt": True, "daily_like_cap": 9}))
            (root / "style.md").write_text("s")
            (root / "persona.md").write_text("p")
            bot.CFG["auto_adopt"] = False  # владелец выключил на панели
            bot.reload_config(root)
            self.assertFalse(bot.CFG["auto_adopt"])
            self.assertEqual(bot.CFG["daily_like_cap"], 9)

    def test_wait_until_returns_false_when_quit_arrives(self):
        bot.GUI = True
        self.bot.cmds.put('{"cmd":"quit"}')
        self.assertFalse(self.bot.wait_until(lambda: False))
        self.assertTrue(self.bot.quit)

    def test_wait_until_returns_true_when_probe_succeeds(self):
        bot.GUI = True
        calls = []
        self.assertTrue(self.bot.wait_until(lambda: calls.append(1) or len(calls) >= 3))
        self.assertEqual(len(calls), 3)

    def test_example_count_per_label_is_clamped(self):
        tmp, d = make_dir([(f"l{i}", "like", f"2026-11-{i + 1:02d}T10:00:00Z") for i in range(14)])
        self.addCleanup(tmp.cleanup)
        self.assertEqual(len(examples.load_examples(d, per_label=100)), examples.MAX_PER_LABEL)
        self.assertEqual(examples.MAX_PER_LABEL, 10)


if __name__ == "__main__":
    unittest.main()
