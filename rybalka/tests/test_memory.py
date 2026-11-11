import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
import bot
import memory
from test_gui_bot import make_bot


class MergeFactsTests(unittest.TestCase):
    def test_adds_new_facts_and_skips_duplicates_ignoring_case_and_spaces(self):
        out = memory.merge_facts(["учится заочно"], ["Учится  заочно", "работает фармацевтом"])
        self.assertEqual(out, ["учится заочно", "работает фармацевтом"])

    def test_ignores_junk_and_overlong_items(self):
        out = memory.merge_facts([], ["", "  ", None, 5, "x" * 200, "живёт в Купчино"])
        self.assertEqual(out, ["живёт в Купчино"])

    def test_keeps_only_the_newest_when_over_the_cap(self):
        out = memory.merge_facts([f"факт {i}" for i in range(memory.MAX_FACTS)], ["самый новый"])
        self.assertEqual(len(out), memory.MAX_FACTS)
        self.assertEqual(out[-1], "самый новый")
        self.assertNotIn("факт 0", out)

    def test_non_list_input_is_ignored(self):
        self.assertEqual(memory.merge_facts(["a"], "не список"), ["a"])
        self.assertEqual(memory.merge_facts(["a"], None), ["a"])


class AskedQuestionsTests(unittest.TestCase):
    def test_collects_only_questions_and_trims_them(self):
        out = memory.merge_asked([], ["ну понятно)", "а кем работаешь?", "ты учишься сейчас или работаешь?)"])
        self.assertEqual(out, ["а кем работаешь?", "ты учишься сейчас или работаешь?)"])

    def test_keeps_the_last_twelve(self):
        out = memory.merge_asked([f"вопрос {i}?" for i in range(12)], ["новый вопрос?"])
        self.assertEqual(len(out), memory.MAX_ASKED)
        self.assertEqual(out[-1], "новый вопрос?")


class MemoryPromptTests(unittest.TestCase):
    def test_prompt_lists_facts_and_earlier_questions(self):
        text = memory.memory_prompt(["работает фармацевтом", "учится заочно"], ["а кем работаешь?"])
        self.assertIn("работает фармацевтом", text)
        self.assertIn("а кем работаешь?", text)
        self.assertIn("facts", text)
        self.assertIn("не спрашивай", text.lower())

    def test_prompt_without_memory_still_forbids_interrogation(self):
        text = memory.memory_prompt([], [])
        self.assertIn("допрос", text.lower())
        self.assertIn("facts", text)


class BotMemoryTests(unittest.TestCase):
    def setUp(self):
        self.old = (bot.STATE_FILE,)
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        bot.STATE_FILE = Path(self.tmp.name) / "state.json"
        self.bot = make_bot()

    def tearDown(self):
        bot.STATE_FILE = self.old[0]
        self.tmp.cleanup()

    def test_memory_prompt_for_a_chat_reads_stored_facts_and_history_questions(self):
        self.bot.state["memory"] = {"anna": {"facts": ["работает фармацевтом"], "asked": []}}
        hist = [{"out": True, "text": "а на кого учишься?\n19:20"}, {"out": False, "text": "на заочке\n19:21"}]
        text = self.bot.memory_prompt_for("anna", hist)
        self.assertIn("работает фармацевтом", text)
        self.assertIn("а на кого учишься?", text)

    def test_remember_merges_model_facts_and_sent_questions_and_persists(self):
        self.bot.remember("anna", {"facts": ["живёт в Купчино", "учится заочно"]}, ["ясно)", "а давно там живёшь?"])
        mem = self.bot.state["memory"]["anna"]
        self.assertEqual(mem["facts"], ["живёт в Купчино", "учится заочно"])
        self.assertEqual(mem["asked"], ["а давно там живёшь?"])
        self.assertTrue(bot.STATE_FILE.exists())

    def test_remember_tolerates_a_reply_without_facts(self):
        self.bot.remember("anna", {"action": "reply"}, ["ну понятно)"])
        self.assertEqual(self.bot.state["memory"]["anna"], {"facts": [], "asked": []})

    def test_memory_is_kept_separately_per_chat(self):
        self.bot.remember("anna", {"facts": ["учится"]}, [])
        self.bot.remember("kira", {"facts": ["работает"]}, [])
        self.assertEqual(self.bot.state["memory"]["anna"]["facts"], ["учится"])
        self.assertEqual(self.bot.state["memory"]["kira"]["facts"], ["работает"])


if __name__ == "__main__":
    unittest.main()
