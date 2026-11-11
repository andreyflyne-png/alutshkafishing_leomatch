"""Зеркало браузера для приложения Russian Fishing 4: кадры страницы наружу, клики и ввод внутрь.

Здесь только чистые помощники (пересчёт координат, ограничения, частота кадров); работа со страницей в bot.py.
"""

KEYS = {"Enter", "Backspace", "Delete", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "Home", "End", "PageUp", "PageDown"}
MAX_TEXT = 500
MIN_SIDE, MAX_SIDE = 400, 2400


def to_pixels(x, y, width, height):
    """Доли 0..1 -> пиксели страницы (с обрезкой по краям)."""
    cx, cy = min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)
    return round(cx * width), round(cy * height)


def clamp_viewport(width, height):
    return (min(max(int(width), MIN_SIDE), MAX_SIDE), min(max(int(height), MIN_SIDE), MAX_SIDE))


def key_allowed(name):
    """Только навигационные клавиши: сочетания вроде Meta+Q приложению пересылать нельзя."""
    return name in KEYS


class FrameGate:
    """Пропускает кадры не чаще fps раз в секунду."""

    def __init__(self, fps):
        self.interval = 1.0 / max(float(fps), 1.0)
        self.last = None

    def allow(self, now):
        if self.last is None or now - self.last >= self.interval:
            self.last = now
            return True
        return False
