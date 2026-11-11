"""Протокол обмена с приложением Russian Fishing 4: JSON Lines (по одному объекту на строку, UTF-8)."""
import json
import sys


def emit(event_type, **fields):
    """Печатает одно событие бота одной JSON-строкой в stdout."""
    sys.stdout.write(json.dumps({"type": event_type, **fields}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def parse_command(line):
    """Разбирает команду приложения. None для пустой или некорректной строки."""
    try:
        obj = json.loads(line)
    except (TypeError, ValueError):
        return None
    if isinstance(obj, dict) and isinstance(obj.get("cmd"), str):
        return obj
    return None
