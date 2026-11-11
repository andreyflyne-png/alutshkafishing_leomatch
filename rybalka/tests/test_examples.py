import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
import examples

JPEG = b"\xff\xd8\xff\xe0fakejpeg"


def make_dir(entries, write_files=True):
    """entries: [(id, label, created)] -> папка с index.json и файлами."""
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)
    items = []
    for id_, label, created in entries:
        items.append({"id": id_, "label": label, "file": f"{id_}.jpg", "created": created})
        if write_files:
            (d / f"{id_}.jpg").write_bytes(JPEG + id_.encode())
    (d / "index.json").write_text(json.dumps({"examples": items}))
    return tmp, d


class LoadExamplesTests(unittest.TestCase):
    def test_picks_newest_per_label_up_to_limit(self):
        tmp, d = make_dir([("l1", "like", "2026-11-01T10:00:00Z"), ("l2", "like", "2026-11-02T10:00:00Z"),
                           ("l3", "like", "2026-11-03T10:00:00Z"), ("s1", "skip", "2026-11-01T10:00:00Z"),
                           ("s2", "skip", "2026-11-02T10:00:00Z")])
        self.addCleanup(tmp.cleanup)
        got = examples.load_examples(d, per_label=2)
        self.assertEqual([(e["label"], e["id"]) for e in got],
                         [("like", "l3"), ("like", "l2"), ("skip", "s2"), ("skip", "s1")])

    def test_skips_missing_files_and_unknown_labels(self):
        tmp, d = make_dir([("a", "like", "2026-11-01T10:00:00Z"), ("b", "maybe", "2026-11-02T10:00:00Z"),
                           ("c", "skip", "2026-11-03T10:00:00Z")])
        self.addCleanup(tmp.cleanup)
        (d / "a.jpg").unlink()
        self.assertEqual([e["id"] for e in examples.load_examples(d, per_label=4)], ["c"])

    def test_missing_or_broken_index_gives_empty_list(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(examples.load_examples(Path(t), 4), [])
            (Path(t) / "index.json").write_text("{ не json")
            self.assertEqual(examples.load_examples(Path(t), 4), [])
            self.assertEqual(examples.load_examples(Path(t) / "нет-папки", 4), [])

    def test_zero_limit_gives_empty_list(self):
        tmp, d = make_dir([("a", "like", "2026-11-01T10:00:00Z")])
        self.addCleanup(tmp.cleanup)
        self.assertEqual(examples.load_examples(d, per_label=0), [])


class BuildBlocksTests(unittest.TestCase):
    def test_empty_gives_no_blocks(self):
        self.assertEqual(examples.build_example_blocks([]), [])

    def test_structure_labels_and_cache_marker(self):
        tmp, d = make_dir([("l1", "like", "2026-11-01T10:00:00Z"), ("s1", "skip", "2026-11-01T10:00:00Z")])
        self.addCleanup(tmp.cleanup)
        blocks = examples.build_example_blocks(examples.load_examples(d, 4))
        self.assertEqual(blocks[0]["type"], "text")
        self.assertIn("18", blocks[0]["text"])  # напоминание: возраст примерами не отменяется
        images = [b for b in blocks if b["type"] == "image"]
        self.assertEqual(len(images), 2)
        self.assertEqual(images[0]["source"]["media_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(images[0]["source"]["data"]), JPEG + b"l1")
        texts = " ".join(b["text"] for b in blocks if b["type"] == "text")
        self.assertIn("ЛАЙКАТЬ", texts)
        self.assertIn("НЕ ЛАЙКАТЬ", texts)
        self.assertEqual(blocks[-1].get("cache_control"), {"type": "ephemeral"})
        self.assertEqual(sum(1 for b in blocks if "cache_control" in b), 1)


if __name__ == "__main__":
    unittest.main()
