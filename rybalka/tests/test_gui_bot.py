import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
import bot


def make_bot():
    """Rybalka без браузера и без клиента API: только состояние и команды."""
    b = bot.Rybalka.__new__(bot.Rybalka)
    b.dry = False
    b.state = {"likes": {}, "usernames": [], "peers": {}, "last_replied": {}, "paused": [],
               "to_open": [], "opened": [], "seen_matches": [], "match_queue": []}
    b.running = False
    b.likes_on = True
    b.chats_on = True
    b.quit = False
    return b


class GuiBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (bot.GUI, bot.emit, bot.STATE_FILE, bot.LOG_FILE, dict(bot.CFG))
        bot.GUI = True
        bot.STATE_FILE = Path(self.tmp.name) / "state.json"
        bot.LOG_FILE = Path(self.tmp.name) / "log.jsonl"
        self.events = []
        bot.emit = lambda event_type, **f: self.events.append({"type": event_type, **f})
        self.bot = make_bot()

    def tearDown(self):
        bot.GUI, bot.emit, bot.STATE_FILE, bot.LOG_FILE = self.old[:4]
        bot.CFG.clear()
        bot.CFG.update(self.old[4])
        self.tmp.cleanup()

    def last(self, t):
        return [e for e in self.events if e["type"] == t][-1]

    def test_command_start_stop_sets_running(self):
        self.bot.handle_command({"cmd": "start"})
        self.assertTrue(self.bot.running)
        self.assertTrue(self.last("status")["running"])
        self.bot.handle_command({"cmd": "stop"})
        self.assertFalse(self.bot.running)
        self.assertFalse(self.last("status")["running"])

    def test_command_set_toggles(self):
        self.bot.handle_command({"cmd": "set", "key": "likes", "value": False})
        self.assertFalse(self.bot.likes_on)
        self.bot.handle_command({"cmd": "set", "key": "chats", "value": False})
        self.assertFalse(self.bot.chats_on)
        self.bot.handle_command({"cmd": "set", "key": "auto", "value": False})
        self.assertFalse(bot.CFG["auto_adopt"])
        st = self.last("status")
        self.assertEqual((st["likes_on"], st["chats_on"], st["auto_adopt"]), (False, False, False))

    def test_command_add_remove_resume(self):
        self.bot.handle_command({"cmd": "add", "user": "@Anna"})
        self.assertIn("Anna", self.bot.state["usernames"])
        self.bot.state["paused"].append("Anna")
        self.bot.handle_command({"cmd": "resume", "user": "Anna"})
        self.assertNotIn("Anna", self.bot.state["paused"])
        self.bot.handle_command({"cmd": "remove", "user": "anna"})
        self.assertNotIn("Anna", self.bot.state["usernames"])
        self.assertIn("anna", self.bot.state["ignored"])
        names = [c["name"] for c in self.last("chats")["items"]]
        self.assertNotIn("Anna", names)

    def test_status_event_fields(self):
        self.bot.emit_status()
        st = self.last("status")
        for key in ("running", "likes_on", "chats_on", "auto_adopt", "dry_run", "likes_today", "cap"):
            self.assertIn(key, st)

    def test_chats_event_states(self):
        self.bot.state["usernames"] = ["a", "b"]
        self.bot.state["paused"] = ["b"]
        self.bot.state["ignored"] = ["c"]
        self.bot.emit_chats()
        items = {c["name"]: c["state"] for c in self.last("chats")["items"]}
        self.assertEqual(items["a"], "active")
        self.assertEqual(items["b"], "paused")
        self.assertEqual(items["c"], "ignored")

    def test_escalate_emits_alert(self):
        bot.log("ESCALATE", user="v_uviii", reason="спросила про бота", they_wrote="ты бот?")
        alert = self.last("alert")
        self.assertEqual((alert["user"], alert["reason"], alert["they_wrote"]),
                         ("v_uviii", "спросила про бота", "ты бот?"))

    def test_log_event_stringifies_fields(self):
        bot.log("profile", like=True, conf=0.8)
        entry = self.last("log")
        self.assertEqual(entry["kind"], "profile")
        self.assertEqual(entry["fields"], {"like": "True", "conf": "0.8"})

    def test_reload_config_rereads_files(self):
        root = Path(self.tmp.name)
        (root / "config.json").write_text(json.dumps({"daily_like_cap": 7}))
        (root / "style.md").write_text("НОВЫЙ СТИЛЬ")
        (root / "persona.md").write_text("НОВАЯ ЛЕГЕНДА")
        bot.reload_config(root)
        self.assertEqual(bot.CFG["daily_like_cap"], 7)
        self.assertEqual(bot.STYLE, "НОВЫЙ СТИЛЬ")
        self.assertEqual(bot.PERSONA, "НОВАЯ ЛЕГЕНДА")

    def test_say_in_gui_mode_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bot.say("текст для консоли")
            bot.bell()
        self.assertEqual(buf.getvalue(), "")

    def test_console_commands_map_to_protocol(self):
        t = self.bot.text_to_cmd
        self.assertEqual(t("start"), {"cmd": "start"})
        self.assertEqual(t("likes off"), {"cmd": "set", "key": "likes", "value": False})
        self.assertEqual(t("auto on"), {"cmd": "set", "key": "auto", "value": True})
        self.assertEqual(t("del @SomeUser"), {"cmd": "remove", "user": "@SomeUser"})
        self.assertEqual(t("resumechat @v"), {"cmd": "resume", "user": "@v"})
        self.assertEqual(t("q"), {"cmd": "quit"})
        self.assertEqual(t("что-то"), {"cmd": "help"})
        self.assertIsNone(t("   "))


if __name__ == "__main__":
    unittest.main()
