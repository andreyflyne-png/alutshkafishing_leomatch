import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import realism


class PickShapeTests(unittest.TestCase):
    def test_a_question_makes_the_next_question_much_rarer(self):
        rng = random.Random(1)
        fresh = sum(realism.pick_shape([], rng).allow_question for _ in range(4000)) / 4000
        after = sum(realism.pick_shape(["как дела?"], rng).allow_question for _ in range(4000)) / 4000
        after_smiley = sum(realism.pick_shape(["ну ты даёшь", "а ты откуда?)"], rng).allow_question for _ in range(4000)) / 4000
        self.assertLess(after, 0.2)
        self.assertLess(after, fresh / 2)
        self.assertLess(after_smiley, 0.2, "«?)» тоже вопрос")

    def test_all_shapes_appear_over_many_draws(self):
        rng = random.Random(7)
        seen = {realism.pick_shape([], rng).name for _ in range(3000)}
        self.assertEqual(seen, {s.name for s in realism.SHAPES})

    def test_question_only_shape_is_not_dominant(self):
        rng = random.Random(3)
        draws = [realism.pick_shape([], rng).name for _ in range(4000)]
        self.assertLess(draws.count("question_only") / len(draws), 0.2)

    def test_share_of_shapes_that_want_a_question_is_moderate(self):
        rng = random.Random(11)
        draws = [realism.pick_shape([], rng) for _ in range(6000)]
        share = sum(s.want_question for s in draws) / len(draws)
        self.assertGreater(share, 0.18)
        self.assertLess(share, 0.32)

    def test_five_replies_without_questions_force_a_question(self):
        rng = random.Random(4)
        for _ in range(300):
            shape = realism.pick_shape(["ахах понял", "ну ты даёшь", "ясно)", "кайф", "ага"], rng)
            self.assertTrue(shape.want_question, shape.name)

    def test_three_replies_without_questions_do_not_force_one_yet(self):
        rng = random.Random(6)
        names = {realism.pick_shape(["ахах понял", "ну ты даёшь", "ясно)"], rng).name for _ in range(600)}
        self.assertTrue(names - {"statement_question", "question_only"}, "до пяти ответов без вопроса принуждения нет")

    def test_replies_come_in_natural_bursts(self):
        """В живой переписке 54% ходов состоят из 2+ сообщений (1: 46%, 2: 27%, 3: 14%, 4+: 13%)."""
        rng = random.Random(12)
        sizes = [realism.pick_shape([], rng).max_messages for _ in range(6000)]
        multi = sum(s >= 2 for s in sizes) / len(sizes)
        self.assertGreater(multi, 0.35)
        self.assertLess(multi, 0.55)
        self.assertEqual(max(sizes), 4)

    def test_sharing_about_yourself_is_a_shape_without_a_question(self):
        shape = next(s for s in realism.SHAPES if s.name == "self_share")
        self.assertFalse(shape.allow_question)
        self.assertGreaterEqual(shape.max_messages, 1)

class IsQuestionTests(unittest.TestCase):
    def test_question_mark_anywhere_counts(self):
        for text in ("а ты откуда?", "а ты откуда?)", "серьёзно??", "чё, правда? ну ладно"):
            self.assertTrue(realism.is_question(text), text)

    def test_question_without_a_mark_is_caught_by_the_opening_word(self):
        for text in ("а ты где учишься", "чё дальше", "как дела", "почему так"):
            self.assertTrue(realism.is_question(text), text)

    def test_statements_are_not_questions(self):
        for text in ("ну понятно)", "ахах классика", "я в колледже, по айти", "что-то я сегодня устал"):
            self.assertFalse(realism.is_question(text), text)

    def test_questions_stay_a_minority_over_a_long_chat(self):
        """Сквозная проверка: цепочка ответов, где каждое решение зависит от предыдущих, не превращается в допрос."""
        rng = random.Random(8)
        history, asked = [], 0
        for _ in range(2000):
            shape = realism.pick_shape(history, rng)
            text = "а ты откуда?)" if shape.want_question or (shape.allow_question and rng.random() < 0.5) else "ну понятно)"
            history.append(text)
            asked += realism.is_question(text)
        self.assertLess(asked / len(history), 0.36)
        self.assertGreater(asked / len(history), 0.18)
        pairs = sum(realism.is_question(a) and realism.is_question(b) for a, b in zip(history, history[1:]))
        self.assertLess(pairs / len(history), 0.10)

    def test_instruction_describes_the_form(self):
        shape = realism.pick_shape([], random.Random(2))
        text = realism.shape_instruction(shape)
        self.assertIn("ФОРМА", text)
        self.assertIn(shape.instruction, text)


class EngagementTests(unittest.TestCase):
    def test_she_asks_about_him_is_high(self):
        self.assertEqual(realism.engagement(["а ты чем занимаешься?", "расскажи про себя ахах"]), "high")

    def test_dry_short_answers_are_low(self):
        self.assertEqual(realism.engagement(["ага", "ну да", "ок"]), "low")

    def test_a_plain_story_without_questions_is_mid(self):
        self.assertEqual(realism.engagement(["я сегодня на учёбе была, устала немного"]), "mid")

    def test_no_messages_is_mid(self):
        self.assertEqual(realism.engagement([]), "mid")

    def test_long_message_with_laughter_and_a_question_is_high(self):
        self.assertEqual(realism.engagement(["ахах ну ты даёшь, я вообще не ожидала такого от тебя, а у тебя так всегда?"]), "high")


class LevelShapeTests(unittest.TestCase):
    def share(self, level, key, n=8000, seed=3):
        rng = random.Random(seed)
        picks = [realism.pick_shape([], rng, level) for _ in range(n)]
        return sum(key(s) for s in picks) / n

    def test_questions_grow_with_her_interest(self):
        low = self.share("low", lambda s: s.want_question)
        mid = self.share("mid", lambda s: s.want_question)
        high = self.share("high", lambda s: s.want_question)
        self.assertLess(low, mid)
        self.assertLess(mid, high)
        self.assertGreater(high, 0.35)
        self.assertLess(low, 0.20)

    def test_mid_level_asks_in_about_a_quarter_to_a_third_of_replies(self):
        mid = self.share("mid", lambda s: s.want_question)
        self.assertGreater(mid, 0.22)
        self.assertLess(mid, 0.32)

    def test_dry_one_word_shapes_fade_when_she_shows_initiative(self):
        low = self.share("low", lambda s: s.name == "reaction")
        high = self.share("high", lambda s: s.name == "reaction")
        self.assertLess(high, low / 2)


class ToneTests(unittest.TestCase):
    def flirty(self, level, n=8000, seed=21):
        rng = random.Random(seed)
        tones = [realism.pick_tone(rng, level).name for _ in range(n)]
        return (tones.count("light") + tones.count("playful")) / n, set(tones)

    def test_flirting_follows_her_initiative(self):
        low, _ = self.flirty("low")
        mid, kinds = self.flirty("mid")
        high, _ = self.flirty("high")
        self.assertLess(low, 0.36)
        self.assertGreater(mid, 0.48)
        self.assertLess(mid, 0.62)
        self.assertGreater(high, 0.78)
        self.assertEqual(kinds, {"plain", "light", "playful"})

    def test_plain_tone_forbids_forced_jokes(self):
        plain = next(t for t in realism.TONES if t.name == "plain")
        text = realism.tone_instruction(plain)
        self.assertIn("ТОН", text)
        self.assertIn("без шуток", text.lower())

    def test_flirty_tones_describe_real_flirting_not_pickup_lines(self):
        for name in ("light", "playful"):
            tone = next(t for t in realism.TONES if t.name == name)
            self.assertIn("симпат", tone.instruction.lower())
            self.assertIn("цвет", realism.TONES[2].instruction)  # запрет сцены с цветами остаётся


class BuildStyleTests(unittest.TestCase):
    def test_build_style_picks_level_shape_and_tone_together(self):
        prompt, shape, tone, level = realism.build_style("ЛЕГЕНДА", ["ага"], ["а ты чем занимаешься?", "расскажи про себя ахах"], random.Random(1))
        self.assertEqual(level, "high")
        self.assertIn(shape, realism.SHAPES)
        self.assertIn(tone, realism.TONES)
        self.assertTrue(prompt.startswith("ЛЕГЕНДА"))
        self.assertIn("не сливайся", prompt.lower())

    def test_build_style_without_history_is_mid_and_has_no_note(self):
        prompt, _shape, _tone, level = realism.build_style("Л", [], [], random.Random(2))
        self.assertEqual(level, "mid")
        self.assertNotIn("не сливайся", prompt.lower())


class StylePromptTests(unittest.TestCase):
    def test_style_prompt_contains_persona_shape_and_tone(self):
        shape, tone = realism.SHAPES[0], realism.TONES[0]
        text = realism.style_prompt("ЛЕГЕНДА", shape, tone, "mid")
        self.assertTrue(text.startswith("ЛЕГЕНДА"))
        self.assertIn("ФОРМА", text)
        self.assertIn("ТОН", text)

    def test_high_interest_tells_the_model_not_to_back_off(self):
        text = realism.style_prompt("Л", realism.SHAPES[0], realism.TONES[1], "high")
        self.assertIn("инициатив", text.lower())
        self.assertIn("не сливайся", text.lower())

    def test_low_interest_tells_the_model_not_to_push(self):
        text = realism.style_prompt("Л", realism.SHAPES[0], realism.TONES[0], "low")
        self.assertIn("не дави", text.lower())

    def test_mid_interest_adds_no_note(self):
        text = realism.style_prompt("Л", realism.SHAPES[0], realism.TONES[0], "mid")
        self.assertNotIn("инициатив", text.lower())


class EnforceTests(unittest.TestCase):
    def shape(self, name):
        return next(s for s in realism.SHAPES if s.name == name)

    def test_limits_message_count(self):
        out = realism.enforce(["один", "два", "три"], self.shape("statement"))
        self.assertEqual(out, ["один"])

    def test_burst_shape_keeps_up_to_four_short_messages(self):
        out = realism.enforce(["один", "два", "три", "четыре", "пять"], self.shape("burst"))
        self.assertEqual(out, ["один", "два", "три", "четыре"])

    def test_reaction_shape_suggests_real_short_replies(self):
        text = self.shape("reaction").instruction
        for word in ("ахах", "давай", "нормально"):
            self.assertIn(word, text)

    def test_two_statement_shape_keeps_two(self):
        self.assertEqual(realism.enforce(["один", "два", "три"], self.shape("two_statements")), ["один", "два"])

    def test_drops_question_message_when_questions_forbidden(self):
        out = realism.enforce(["ахах понял", "а ты откуда?"], self.shape("two_statements"))
        self.assertEqual(out, ["ахах понял"])

    def test_strips_question_mark_when_every_message_is_a_question(self):
        out = realism.enforce(["и чё дальше?"], self.shape("statement"))
        self.assertEqual(out, ["и чё дальше"])

    def test_question_with_a_smiley_after_the_mark_is_removed_too(self):
        out = realism.enforce(["ну понятно)", "а ты откуда?)"], self.shape("statement"))
        self.assertEqual(out, ["ну понятно)"])

    def test_question_allowed_shape_keeps_the_question(self):
        out = realism.enforce(["ну ты даёшь", "серьёзно?"], self.shape("statement_question"))
        self.assertEqual(out, ["ну ты даёшь"])  # одно сообщение: берём первое
        self.assertEqual(realism.enforce(["серьёзно?"], self.shape("reaction")), ["серьёзно?"])

    def test_empty_input_stays_empty(self):
        self.assertEqual(realism.enforce([], self.shape("statement")), [])
        self.assertEqual(realism.enforce(["  ", ""], self.shape("statement")), [])


class RecentOutgoingTests(unittest.TestCase):
    def test_takes_last_outgoing_texts_in_order(self):
        hist = [{"out": True, "text": "раз\n14:00"}, {"out": False, "text": "привет\n14:01"},
                {"out": True, "text": "два?\n14:02"}, {"out": True, "text": ""}]
        self.assertEqual(realism.recent_outgoing(hist, 5), ["раз", "два?"])
        self.assertEqual(realism.recent_outgoing(hist, 1), ["два?"])


class StaleTopicTests(unittest.TestCase):
    def test_sleep_topic_gets_stale(self):
        texts = ["мне снился странный сон", "ахах и что там было", "что я кудрявая", "к чему такие сны снятся",
                 "спросила у помощника", "а ты часто сны запоминаешь", "иногда", "а тебе кошмары снятся"]
        self.assertEqual(realism.stale_topic(texts, [False, True] * 4), "сон и сны")

    def test_varied_chat_is_not_stale(self):
        texts = ["как дела", "норм", "работаешь сегодня", "да до шести", "а ты", "я дома", "понял", "сериал смотрю"]
        self.assertIsNone(realism.stale_topic(texts, [False, True] * 4))

    def test_short_chat_is_not_stale(self):
        self.assertIsNone(realism.stale_topic(["сон", "сны", "сон"], [False, True, False]))


if __name__ == "__main__":
    unittest.main()
