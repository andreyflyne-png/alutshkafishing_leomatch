import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gui_io


class EmitTests(unittest.TestCase):
    def capture(self, *a, **k):
        buf = io.StringIO()
        with redirect_stdout(buf):
            gui_io.emit(*a, **k)
        return buf.getvalue()

    def test_emit_prints_one_json_line(self):
        out = self.capture("status", running=True)
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out), {"type": "status", "running": True})

    def test_emit_keeps_cyrillic(self):
        out = self.capture("log", text="привет, えっ?")
        self.assertNotIn("\\u", out)
        self.assertIn("привет", out)


class ParseCommandTests(unittest.TestCase):
    def test_parse_command_ok(self):
        cmd = gui_io.parse_command('{"cmd":"set","key":"likes","value":true}')
        self.assertEqual(cmd, {"cmd": "set", "key": "likes", "value": True})

    def test_parse_command_rejects_garbage(self):
        for line in ["", "abc", '{"x":1}', "[1]", '{"cmd":5}']:
            self.assertIsNone(gui_io.parse_command(line), line)


if __name__ == "__main__":
    unittest.main()
