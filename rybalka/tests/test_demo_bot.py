import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "bin", "python")


def spawn():
    return subprocess.Popen([PY, os.path.join(ROOT, "bot.py"), "--gui", "--demo"], cwd=ROOT, text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def reap(p):
    p.kill()
    p.wait()
    for f in (p.stdin, p.stdout, p.stderr):
        if f:
            f.close()


class DemoBotTests(unittest.TestCase):
    def test_demo_emits_ready_then_status(self):
        p = spawn()
        try:
            types = [json.loads(p.stdout.readline())["type"] for _ in range(3)]
        finally:
            reap(p)
        self.assertEqual(types, ["ready", "chats", "status"])

    def test_demo_quit_exits_zero(self):
        p = spawn()
        p.stdout.readline()
        p.stdin.write('{"cmd":"quit"}\n')
        p.stdin.flush()
        code = p.wait(timeout=3)
        reap(p)
        self.assertEqual(code, 0)

    def test_demo_exits_when_parent_closes_stdin(self):
        """Приложение закрылось или упало: поток ввода закрыт, бот не должен оставаться сиротой."""
        p = spawn()
        p.stdout.readline()
        p.stdin.close()
        code = p.wait(timeout=3)
        for f in (p.stdout, p.stderr):
            f.close()
        self.assertEqual(code, 0)

    def test_demo_mirror_streams_frames_and_echoes_input(self):
        p = spawn()
        try:
            for _ in range(3):
                p.stdout.readline()
            p.stdin.write('{"cmd":"mirror","on":true,"w":700,"h":600}\n')
            p.stdin.write('{"cmd":"input","kind":"click","x":0.5,"y":0.5}\n')
            p.stdin.flush()
            events = []
            for _ in range(30):
                events.append(json.loads(p.stdout.readline()))
                if any(e["type"] == "frame" for e in events) and any(
                        e["type"] == "log" and e.get("kind") == "mirror_input" for e in events):
                    break
        finally:
            reap(p)
        frame = next(e for e in events if e["type"] == "frame")
        self.assertEqual((frame["w"], frame["h"]), (700, 600))
        self.assertTrue(os.path.exists(os.path.join(ROOT, "data", "screen.jpg")))
        self.assertTrue(any(e["type"] == "log" and e.get("kind") == "mirror_input" for e in events))

    def test_demo_start_produces_logs(self):
        p = spawn()
        try:
            for _ in range(3):
                p.stdout.readline()
            p.stdin.write('{"cmd":"start"}\n')
            p.stdin.flush()
            events = [json.loads(p.stdout.readline()) for _ in range(6)]
        finally:
            reap(p)
        self.assertIn("log", [e["type"] for e in events])
        self.assertTrue(any(e["type"] == "status" and e["running"] for e in events))


if __name__ == "__main__":
    unittest.main()
