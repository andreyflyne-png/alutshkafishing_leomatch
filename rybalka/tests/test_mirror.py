import base64
import os
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
import bot
import mirror
from test_gui_bot import make_bot


class FakeMouse:
    def __init__(self): self.calls = []
    def click(self, x, y, click_count=1): self.calls.append(("click", x, y, click_count))
    def move(self, x, y): self.calls.append(("move", x, y))
    def wheel(self, dx, dy): self.calls.append(("wheel", dx, dy))


class FakeKeyboard:
    def __init__(self): self.calls = []
    def press(self, key): self.calls.append(("press", key))
    def type(self, text, delay=0): self.calls.append(("type", text))


class FakePage:
    def __init__(self):
        self.viewport_size = {"width": 1000, "height": 800}
        self.mouse, self.keyboard = FakeMouse(), FakeKeyboard()
        self.viewports, self.waits = [], []
    def set_viewport_size(self, size): self.viewports.append(size); self.viewport_size = size
    def wait_for_timeout(self, ms): self.waits.append(ms)


class FakeCdp:
    def __init__(self): self.sent, self.handlers = [], {}
    def send(self, method, params=None): self.sent.append((method, params))
    def on(self, event, handler): self.handlers[event] = handler


class FakeCtx:
    def __init__(self): self.cdp = FakeCdp()
    def new_cdp_session(self, page): return self.cdp


def mirror_bot():
    b = make_bot()
    b.page, b.ctx = FakePage(), FakeCtx()
    b.cdp, b.mirror_active, b.mirror_wanted = None, False, None
    b.frame_n, b.frame_gate, b.manual_until = 0, mirror.FrameGate(8), 0
    b.cmds = queue.Queue()
    return b


class PureHelpersTests(unittest.TestCase):
    def test_to_pixels_scales_and_clamps(self):
        self.assertEqual(mirror.to_pixels(0.5, 0.25, 1000, 800), (500, 200))
        self.assertEqual(mirror.to_pixels(-3, 9, 1000, 800), (0, 800))

    def test_clamp_viewport_bounds(self):
        self.assertEqual(mirror.clamp_viewport(10, 10), (400, 400))
        self.assertEqual(mirror.clamp_viewport(99999, 99999), (2400, 2400))
        self.assertEqual(mirror.clamp_viewport(700, 600), (700, 600))

    def test_frame_gate_limits_rate(self):
        gate = mirror.FrameGate(8)
        self.assertTrue(gate.allow(100.0))
        self.assertFalse(gate.allow(100.05))
        self.assertTrue(gate.allow(100.13))

    def test_key_whitelist(self):
        self.assertTrue(mirror.key_allowed("Enter"))
        self.assertTrue(mirror.key_allowed("ArrowDown"))
        for bad in ("F12", "Meta+Q", "Control+W", "", "a"):
            self.assertFalse(mirror.key_allowed(bad), bad)


class MirrorBotTests(unittest.TestCase):
    def setUp(self):
        self.old = (bot.GUI, bot.emit, bot.SCREEN_FILE, dict(bot.CFG))
        bot.GUI = True
        self.events = []
        bot.emit = lambda event_type, **f: self.events.append({"type": event_type, **f})
        self.tmp = tempfile.TemporaryDirectory()
        bot.SCREEN_FILE = Path(self.tmp.name) / "screen.jpg"
        self.bot = mirror_bot()

    def tearDown(self):
        bot.GUI, bot.emit, bot.SCREEN_FILE = self.old[:3]
        bot.CFG.clear(); bot.CFG.update(self.old[3])
        self.tmp.cleanup()

    def test_click_maps_fractions_to_viewport_pixels(self):
        self.bot.handle_command({"cmd": "input", "kind": "click", "x": 0.5, "y": 0.25})
        self.assertEqual(self.bot.page.mouse.calls, [("click", 500, 200, 1)])

    def test_double_click_uses_click_count_two(self):
        self.bot.handle_command({"cmd": "input", "kind": "dblclick", "x": 0.1, "y": 0.1})
        self.assertEqual(self.bot.page.mouse.calls, [("click", 100, 80, 2)])

    def test_scroll_moves_then_wheels(self):
        self.bot.handle_command({"cmd": "input", "kind": "scroll", "x": 0.5, "y": 0.5, "dx": 0, "dy": 120})
        self.assertEqual(self.bot.page.mouse.calls, [("move", 500, 400), ("wheel", 0, 120)])

    def test_key_whitelist_is_enforced(self):
        self.bot.handle_command({"cmd": "input", "kind": "key", "name": "Enter"})
        self.bot.handle_command({"cmd": "input", "kind": "key", "name": "Meta+Q"})
        self.assertEqual(self.bot.page.keyboard.calls, [("press", "Enter")])

    def test_text_is_typed_and_capped(self):
        self.bot.handle_command({"cmd": "input", "kind": "text", "text": "привет, えっ?"})
        self.bot.handle_command({"cmd": "input", "kind": "text", "text": "я" * 900})
        self.assertEqual(self.bot.page.keyboard.calls[0], ("type", "привет, えっ?"))
        self.assertEqual(len(self.bot.page.keyboard.calls[1][1]), 500)

    def test_malformed_input_is_ignored(self):
        for cmd in ({"cmd": "input", "kind": "click"}, {"cmd": "input", "kind": "explode", "x": 0, "y": 0},
                    {"cmd": "input", "x": 0.5, "y": 0.5}, {"cmd": "input", "kind": "click", "x": "a", "y": None}):
            self.bot.handle_command(cmd)
        self.assertEqual(self.bot.page.mouse.calls, [])

    def test_input_without_page_does_not_crash(self):
        self.bot.page = None
        self.bot.handle_command({"cmd": "input", "kind": "click", "x": 0.5, "y": 0.5})

    def test_manual_input_starts_a_pause(self):
        self.assertFalse(self.bot.in_manual_pause())
        self.bot.handle_command({"cmd": "input", "kind": "click", "x": 0.5, "y": 0.5})
        self.assertTrue(self.bot.in_manual_pause())
        self.bot.manual_until = time.time() - 1
        self.assertFalse(self.bot.in_manual_pause())

    def test_mirror_on_sets_viewport_and_starts_screencast(self):
        self.bot.handle_command({"cmd": "mirror", "on": True, "w": 700, "h": 600})
        self.assertEqual(self.bot.page.viewports[-1], {"width": 700, "height": 600})
        method, params = self.bot.ctx.cdp.sent[-1]
        self.assertEqual(method, "Page.startScreencast")
        self.assertEqual((params["maxWidth"], params["maxHeight"], params["format"]), (700, 600, "jpeg"))

    def test_mirror_off_stops_screencast(self):
        self.bot.handle_command({"cmd": "mirror", "on": True, "w": 700, "h": 600})
        self.bot.handle_command({"cmd": "mirror", "on": False})
        self.assertEqual(self.bot.ctx.cdp.sent[-1][0], "Page.stopScreencast")

    def test_mirror_resize_restarts_with_new_size(self):
        self.bot.handle_command({"cmd": "mirror", "on": True, "w": 700, "h": 600})
        self.bot.handle_command({"cmd": "mirror", "on": True, "w": 800, "h": 650})
        methods = [m for m, _ in self.bot.ctx.cdp.sent]
        self.assertEqual(methods, ["Page.startScreencast", "Page.stopScreencast", "Page.startScreencast"])

    def test_mirror_requested_before_page_exists_is_applied_later(self):
        self.bot.page = None
        self.bot.handle_command({"cmd": "mirror", "on": True, "w": 700, "h": 600})
        self.assertEqual(self.bot.mirror_wanted, (True, 700, 600))
        self.bot.page = FakePage()
        self.bot.apply_mirror_wanted()
        self.assertEqual(self.bot.ctx.cdp.sent[-1][0], "Page.startScreencast")

    def test_frame_is_saved_announced_and_acked(self):
        self.bot.handle_command({"cmd": "mirror", "on": True, "w": 700, "h": 600})
        payload = {"data": base64.b64encode(b"JPEGDATA").decode(), "sessionId": 7,
                   "metadata": {"deviceWidth": 700, "deviceHeight": 600}}
        self.bot.handle_frame(payload)
        self.assertEqual(bot.SCREEN_FILE.read_bytes(), b"JPEGDATA")
        frames = [e for e in self.events if e["type"] == "frame"]
        self.assertEqual(frames, [{"type": "frame", "n": 1, "w": 700, "h": 600}])
        self.assertIn(("Page.screencastFrameAck", {"sessionId": 7}), self.bot.ctx.cdp.sent)

    def test_frames_above_the_rate_limit_are_acked_but_not_announced(self):
        self.bot.handle_command({"cmd": "mirror", "on": True, "w": 700, "h": 600})
        payload = {"data": base64.b64encode(b"X").decode(), "sessionId": 1, "metadata": {}}
        self.bot.handle_frame(payload)
        self.bot.handle_frame(dict(payload, sessionId=2))
        self.assertEqual(len([e for e in self.events if e["type"] == "frame"]), 1)
        acks = [p["sessionId"] for m, p in self.bot.ctx.cdp.sent if m == "Page.screencastFrameAck"]
        self.assertEqual(acks, [1, 2])

    def test_pause_uses_the_page_so_cdp_events_keep_flowing(self):
        real_sleep = time.sleep
        time.sleep = lambda s: (_ for _ in ()).throw(AssertionError("time.sleep остановил бы поток кадров"))
        try:
            self.bot.running = True
            self.bot.nap(0.25)
        finally:
            time.sleep = real_sleep
        self.assertTrue(self.bot.page.waits)

    def test_pause_without_a_page_falls_back_to_sleep(self):
        self.bot.page = None
        started = time.time()
        self.bot.pause(0.05)
        self.assertGreaterEqual(time.time() - started, 0.04)


if __name__ == "__main__":
    unittest.main()
