import os
import queue
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
import bot
from test_gui_bot import make_bot


class Clock:
    def __init__(self): self.now = 1000.0
    def time(self): return self.now
    def advance(self, s): self.now += s


class WaitForReplyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.t0 = self.clock.now
        self.old_cfg = dict(bot.CFG)
        self.old_log = bot.log
        bot.log = lambda *a, **k: None
        bot.CFG["chat_wait_seconds"] = 30
        self.patch = mock.patch("time.time", self.clock.time)
        self.patch.start()
        self.b = make_bot()
        self.b.cmds = queue.Queue()
        self.b.running = True
        self.answer_calls, self.nap_calls = [], 0
        self.her_message_at, self.answered = None, False
        self.other_unread_at = None
        self.b.page = types.SimpleNamespace(evaluate=self.evaluate)
        self.b.nap = self.nap
        self.b.unread_queue = self.unread_queue
        self.b.answer = self.answer

    def tearDown(self):
        self.patch.stop()
        bot.CFG.clear(); bot.CFG.update(self.old_cfg)
        bot.log = self.old_log

    def elapsed(self): return self.clock.now - self.t0

    def nap(self, s):
        self.nap_calls += 1
        self.clock.advance(s)
        return self.b.running

    def unread_queue(self):
        return ["kira"] if self.other_unread_at is not None and self.elapsed() >= self.other_unread_at else []

    def evaluate(self, js, *a):
        she_wrote = self.her_message_at is not None and self.elapsed() >= self.her_message_at and not self.answered
        return [{"mid": 1, "out": not she_wrote, "text": "привет", "photo": False, "sticker": False}]

    def answer(self, u):
        self.answer_calls.append(self.elapsed())
        self.answered = True
        return True

    def test_returns_to_likes_after_30_seconds_of_silence(self):
        self.b.wait_for_reply("anna")
        self.assertGreaterEqual(self.elapsed(), 30)
        self.assertLess(self.elapsed(), 32)
        self.assertEqual(self.answer_calls, [])

    def test_her_reply_is_answered_and_the_timer_restarts(self):
        self.her_message_at = 5
        self.b.wait_for_reply("anna")
        self.assertEqual(len(self.answer_calls), 1)
        self.assertGreaterEqual(self.elapsed(), 35)  # 5 с до ответа + ещё 30 тишины
        self.assertLess(self.elapsed(), 38)

    def test_another_chat_writing_interrupts_the_wait(self):
        self.other_unread_at = 3
        self.b.wait_for_reply("anna")
        self.assertLess(self.elapsed(), 6)

    def test_stop_ends_the_wait_immediately(self):
        self.b.running = False
        self.b.wait_for_reply("anna")
        self.assertEqual(self.nap_calls, 1)

    def test_wait_length_comes_from_config(self):
        bot.CFG["chat_wait_seconds"] = 10
        self.b.wait_for_reply("anna")
        self.assertGreaterEqual(self.elapsed(), 10)
        self.assertLess(self.elapsed(), 12)

    def test_zero_seconds_turns_the_wait_off(self):
        bot.CFG["chat_wait_seconds"] = 0
        self.b.wait_for_reply("anna")
        self.assertEqual(self.nap_calls, 0)


class ReplyLoopWaitsTests(unittest.TestCase):
    def setUp(self):
        self.old_log = bot.log
        bot.log = lambda *a, **k: None

    def tearDown(self):
        bot.log = self.old_log

    def test_reply_loop_waits_in_the_chat_after_a_successful_answer(self):
        b = make_bot()
        b.running = True
        b.last_sweep = time.time()
        b.refresh_peer_ids = lambda: None
        b.adopt_new_people = lambda: None
        b.log_unknown_unread = lambda: None
        queues = iter([["anna"], []])
        b.unread_queue = lambda: next(queues)
        b.answer = lambda u: True
        waited = []
        b.wait_for_reply = lambda u: waited.append(u)
        b.reply_loop()
        self.assertEqual(waited, ["anna"])

    def test_reply_loop_does_not_wait_when_nothing_was_sent(self):
        b = make_bot()
        b.running = True
        b.last_sweep = time.time()
        b.refresh_peer_ids = lambda: None
        b.adopt_new_people = lambda: None
        b.log_unknown_unread = lambda: None
        queues = iter([["anna"], []])
        b.unread_queue = lambda: next(queues)
        b.answer = lambda u: False
        waited = []
        b.wait_for_reply = lambda u: waited.append(u)
        b.reply_loop()
        self.assertEqual(waited, [])


if __name__ == "__main__":
    unittest.main()
