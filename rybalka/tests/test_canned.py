import unittest

import canned


class CannedTests(unittest.TestCase):
    def test_exact_phrase_gets_error_text(self):
        r = canned.canned_reply("Скинь фото!")
        self.assertTrue(r.startswith("ERROR [503]: Service Unavailable"))
        self.assertTrue(r.endswith("Retry scheduled in 30 seconds..."))

    def test_case_and_ending_matter(self):
        for t in ("скинь фото!", "Скинь фото", "Скинь фото!!", "Скинь фото.", "Скинь фото ещё!", "", None):
            self.assertIsNone(canned.canned_reply(t), t)


if __name__ == "__main__":
    unittest.main()
