import base64
import json
import os
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
        text = '{"like": true, "confidence": 0.9, "reason": "ок"}'
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=text)], stop_reason="end_turn")


class JudgeExamplesTests(unittest.TestCase):
    def setUp(self):
        self.old = (bot.EXAMPLES, dict(bot.CFG), bot.log, bot.STYLE, bot.PERSONA)
        bot.log = lambda *a, **k: None
        self.bot = make_bot()
        self.bot.client = FakeClient()

    def tearDown(self):
        bot.EXAMPLES, cfg, bot.log, bot.STYLE, bot.PERSONA = self.old
        bot.CFG.clear()
        bot.CFG.update(cfg)

    def content(self):
        return self.bot.client.calls[-1]["messages"][0]["content"]

    def test_judge_sends_examples_before_the_profile(self):
        tmp, d = make_dir([("l1", "like", "2026-11-01T10:00:00Z"), ("s1", "skip", "2026-11-01T10:00:00Z")])
        self.addCleanup(tmp.cleanup)
        bot.EXAMPLES = examples.load_examples(d, 4)
        res = self.bot.judge(b"PROFILE", "Аня, 18, Санкт-Петербург")
        self.assertTrue(res["like"])
        blocks = self.content()
        self.assertEqual(blocks[0]["type"], "text")
        self.assertIn("владелец", blocks[0]["text"])
        images = [b for b in blocks if b["type"] == "image"]
        self.assertEqual(len(images), 3)  # два примера и сама анкета
        self.assertEqual(base64.b64decode(images[-1]["source"]["data"]), b"PROFILE")
        self.assertEqual(blocks[-2]["type"], "image")
        self.assertIn("Аня, 18", blocks[-1]["text"])
        marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
        self.assertEqual(marked, [len(blocks) - 3])  # маркер на последнем блоке примеров, не на анкете

    def test_judge_without_examples_sends_profile_only(self):
        bot.EXAMPLES = []
        self.bot.judge(b"PROFILE", "Аня, 18, Спб")
        blocks = self.content()
        self.assertEqual([b["type"] for b in blocks], ["image", "text"])

    def test_age_rule_blocks_before_any_model_call(self):
        tmp, d = make_dir([("l1", "like", "2026-11-01T10:00:00Z")])
        self.addCleanup(tmp.cleanup)
        bot.EXAMPLES = examples.load_examples(d, 4)
        res = self.bot.judge(b"PROFILE", "Аня, 17, Спб")
        self.assertFalse(res["like"])
        self.assertEqual(self.bot.client.calls, [])

    def test_reload_config_reloads_examples(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "config.json").write_text(json.dumps({"training_examples_per_label": 1, "daily_like_cap": 5}))
            (root / "style.md").write_text("s")
            (root / "persona.md").write_text("p")
            ex = root / "data" / "examples"
            ex.mkdir(parents=True)
            (ex / "a.jpg").write_bytes(b"x")
            (ex / "b.jpg").write_bytes(b"y")
            (ex / "index.json").write_text(json.dumps({"examples": [
                {"id": "a", "label": "like", "file": "a.jpg", "created": "2026-11-01T10:00:00Z"},
                {"id": "b", "label": "like", "file": "b.jpg", "created": "2026-11-02T10:00:00Z"}]}))
            bot.reload_config(root)
            self.assertEqual([e["id"] for e in bot.EXAMPLES], ["b"])


if __name__ == "__main__":
    unittest.main()
