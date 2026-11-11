"""Примеры анкет от владельца («лайкать» / «не лайкать»): отбор и сборка блоков запроса.

Формат: <папка>/index.json = {"examples": [{"id", "label": "like"|"skip", "file", "created": ISO-8601}]},
рядом лежат файлы изображений. Папку ведёт приложение Russian Fishing 4.
"""
import base64
import json
from pathlib import Path

MAX_PER_LABEL = 10  # предел примеров каждого вида: у запроса к API есть лимит на число изображений
LABEL_TITLES = {"like": "ЛАЙКАТЬ", "skip": "НЕ ЛАЙКАТЬ"}
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

INTRO = ("Ниже примеры анкет, которые выбрал владелец: одни он хочет лайкать, другие нет. Ориентируйся на них при оценке "
         "стиля. Примеры не отменяют правило возраста: анкеты младше 18 лет или без ясного возраста всегда НЕ ЛАЙКАТЬ.")


def load_examples(folder, per_label=4):
    """До per_label самых свежих примеров каждого вида (сначала like, потом skip). Битые записи пропускаются."""
    folder = Path(folder)
    per_label = min(per_label, MAX_PER_LABEL)
    if per_label <= 0:
        return []
    try:
        items = json.loads((folder / "index.json").read_text()).get("examples", [])
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for label in ("like", "skip"):
        picked = sorted((i for i in items if isinstance(i, dict) and i.get("label") == label),
                        key=lambda i: str(i.get("created", "")), reverse=True)
        for item in picked:
            path = folder / str(item.get("file", ""))
            if not path.is_file():
                continue
            out.append({"id": item.get("id"), "label": label, "path": path})
            if sum(1 for e in out if e["label"] == label) >= per_label:
                break
    return out


def build_example_blocks(loaded):
    """Блоки content для запроса: вступление, затем «картинка + подпись» на каждый пример.
    Последний блок помечается для кэша запросов: примеры одни и те же от анкеты к анкете."""
    if not loaded:
        return []
    blocks = [{"type": "text", "text": INTRO}]
    for n, e in enumerate(loaded, 1):
        media = MEDIA_TYPES.get(e["path"].suffix.lower(), "image/jpeg")
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                   "data": base64.b64encode(e["path"].read_bytes()).decode()}})
        blocks.append({"type": "text", "text": f"Пример {n}: {LABEL_TITLES[e['label']]}"})
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks
