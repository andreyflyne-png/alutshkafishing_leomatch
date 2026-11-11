"""Естественность переписки: форма каждого ответа выбирается случайно, чтобы не было шаблона «реакция + вопрос».

pick_shape решает, как ответить (одним утверждением, реакцией, двумя репликами...), shape_instruction передаёт это модели,
enforce подрезает ответ до выбранной формы (число сообщений, вопросы).
"""
import random
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Shape:
    name: str
    weight: int
    instruction: str
    max_messages: int
    allow_question: bool
    want_question: bool = False


@dataclass(frozen=True)
class Tone:
    name: str
    weight: int
    instruction: str


# Доли подогнаны под реальную переписку (5 дней живого чата, 4136 сообщений): вопрос примерно в каждом пятом-четвёртом ходе,
# 54% ходов состоят из 2+ сообщений (1: 46%, 2: 27%, 3: 14%, 4+: 13%), медиана сообщения 5 слов, четверть из 1-2 слов.
SHAPES = [
    Shape("statement", 20, "ОДНО короткое сообщение-утверждение: реакция или комментарий по делу, 3-8 слов. БЕЗ вопроса.", 1, False),
    Shape("reaction", 12, "Совсем коротко, 1-2 слова, как в живой переписке (например: «ахах», «да», «давай», «ща», «нормально», «окей», "
                          "«ага», «кайф», «жду», «да не»). Без развёрнутых фраз.", 1, True),
    Shape("self_share", 10, "Коротко расскажи что-то о себе по теме разговора (из легенды: учёба, зал, Dota, прогулки), одним-двумя сообщениями. БЕЗ вопроса.", 2, False),
    Shape("statement_question", 12, "ОДНО сообщение: короткая реплика и в конце ОДИН короткий вопрос по теме разговора, со знаком «?» в конце.", 1, True, True),
    Shape("question_only", 8, "ОДИН короткий вопрос по её последней фразе, без утверждений, со знаком «?» в конце.", 1, True, True),
    Shape("two_statements", 19, "ДВА коротких сообщения подряд, оба БЕЗ вопросов (мысль и её продолжение).", 2, False),
    Shape("burst", 19, "ТРИ-ЧЕТЫРЕ совсем коротких сообщения подряд, как в живой переписке (мысль, продолжение, реакция), каждое 1-6 слов. БЕЗ вопросов.", 4, False),
]

# Тон ответа. Сколько флирта, зависит от того, как ведёт себя она (см. engagement): холодная, обычная, проявляет инициативу.
TONES = [
    Tone("plain", 60, "Без шуток и без флирта: обычный живой ответ по делу, коротко и буднично. Не придумывай подколов и интриг."),
    Tone("light", 28, "Проявляй симпатию: тёплая реплика или лёгкий комплимент по делу (по тому, что она сказала), заигрывание без пошлости. "
                      "Коротко, как в телеге. Без пикапа и заготовок. Если есть за что, добавь добрый подкол над тем, что она сказала."),
    Tone("playful", 12, "Флиртуй открыто, но коротко и естественно: подкол, комплимент, намёк на симпатию и желание пообщаться ближе. "
                        "Обязательно с добрым подколом над её словами (тепло, с улыбкой, не обидно). "
                        "Никаких заготовок, банальщины и сцен вроде «придёшь меня встречать с цветами»."),
]
# plain / light / playful для каждого уровня интереса (флирт около 30% / 55% / 85% ответов)
TONE_WEIGHTS = {"low": (70, 22, 8), "mid": (45, 38, 17), "high": (15, 45, 40)}
# Множители форм: чем больше она заинтересована, тем больше встречных вопросов и тем меньше сухих односложных ответов.
LEVEL_MULT = {
    "low": {"statement_question": 0.7, "question_only": 0.6, "burst": 1.2},
    "mid": {"statement_question": 1.4, "question_only": 1.2},
    "high": {"reaction": 0.3, "statement": 0.6, "statement_question": 2.2, "question_only": 1.6, "burst": 0.6},
}

# Начало короткой фразы без знака вопроса, которое тоже вопрос («а ты где учишься», «чё дальше»).
_OPENERS = re.compile(r"^(?:а|и|ну)?\s*(?:ты|вы)?\s*(?:как|где|когда|почему|зачем|сколько|чем|кем|кто|что|чё|че|куда|откуда|"
                      r"какой|какая|какие|какое|на кого|правда|серьёзно)(?![\w-])", re.I)


def is_question(text):
    """Вопрос: есть «?» где угодно («?)», «??») либо короткая фраза начинается с вопросительного слова."""
    t = (text or "").strip()
    return "?" in t or (len(t) <= 80 and bool(_OPENERS.match(t.lower())))


ends_with_question = is_question  # прежнее имя


_ABOUT_HIM = re.compile(r"(?<!\w)(?:ты|тебя|тебе|тобой|твой|твоя|твои|вы|вас|вам)(?!\w)", re.I)
_LAUGH = re.compile(r"ахах|хаха|хах|😂|😍|❤|🥰|😘|\)", re.I)
# Её прямой интерес: комплименты, симпатия, зовёт встретиться, говорит о «нас»
_INTEREST = re.compile(r"красив|симпат|нравишь|нравится|скуч|хочу|встрет|увиде|погуля|свободн|для тебя|с тобой|милый|классный|"
                       r"обожаю|прикольн\w* ты|ты (?:такой|тако)|не против|давай", re.I)


def engagement(her_texts):
    """Насколько собеседница проявляет интерес и инициативу: low (сухо), mid, high (спрашивает о нём, пишет развёрнуто, шутит)."""
    texts = [t for t in her_texts if isinstance(t, str) and t.strip()][-4:]
    if not texts:
        return "mid"
    words = [len(re.findall(r"\w+", t)) for t in texts]
    avg = sum(words) / len(words)
    score = 0.0
    if any(is_question(t) and _ABOUT_HIM.search(t) for t in texts):
        score += 0.35  # спрашивает о нём
    if avg >= 6:
        score += 0.25
    elif avg >= 2.5:
        score += 0.12  # обычные короткие сообщения в 3-5 слов это не сухость
    if any(is_question(t) for t in texts):
        score += 0.15
    if any(w >= 10 for w in words):
        score += 0.15
    if any(_LAUGH.search(t) for t in texts):
        score += 0.10
    if any(_ABOUT_HIM.search(t) for t in texts):
        score += 0.10  # обращается к нему, даже без вопроса
    if any(_INTEREST.search(t) for t in texts):
        score += 0.30  # комплимент, симпатия, зовёт встретиться
    return "high" if score >= 0.5 else ("low" if score < 0.10 else "mid")  # low: односложно, без вопросов, без смеха


DRIVE_MESSAGES = 6  # первые столько своих сообщений бот активно развивает разговор, а не просто реагирует
# В начале реже тупиковые формы (голая реакция, утверждение без зацепки), чаще вопросы и истории о себе
DRIVE_MULT = {"reaction": 0.1, "statement": 0.25, "burst": 0.3, "two_statements": 0.5,
              "statement_question": 1.8, "question_only": 1.5, "self_share": 1.5}
DRIVE_NOTE = ("\nНАЧАЛО ПЕРЕПИСКИ: твоя задача сейчас не дать ей уйти. Каждое сообщение должно давать ей за что зацепиться: живой вопрос по теме, "
              "интересная деталь о себе с продолжением или подхват её темы. Не пиши тупиковых реплик («ага», «понятно», «ну да»), на которые "
              "нечего ответить. Если она ответила односложно, сам предложи тему или расскажи что-то короткое и цепляющее. Без допроса: "
              "один вопрос максимум, и такой, на который легко и приятно отвечать.")


def pick_shape(recent_out, rng=random, level="mid", drive=False):
    """Форма следующего ответа с учётом того, что бот уже писал.
    После вопроса следующий вопрос заметно реже (но не запрещён: у людей вопросы бывают и подряд);
    пять ответов подряд без вопроса: пора спросить."""
    flags = [is_question(t) for t in recent_out[-5:]]
    if len(flags) == 5 and not any(flags):
        candidates = [s for s in SHAPES if s.want_question]
        return rng.choices(candidates, weights=[s.weight for s in candidates], k=1)[0]
    last_was_question = bool(flags) and flags[-1]
    mult = LEVEL_MULT.get(level, {})
    weights = [s.weight * mult.get(s.name, 1) * (DRIVE_MULT.get(s.name, 1) if drive else 1)
               * (0.3 if last_was_question and s.allow_question else 1) for s in SHAPES]
    return rng.choices(SHAPES, weights=weights, k=1)[0]


EARLY_MESSAGES = 10  # столько своих сообщений длится «начало переписки»
EARLY_WEIGHTS = (10, 45, 45)  # в самом начале флирта больше всего (около 90% ответов)


def warmth(our_count, level):
    """Насколько усилен флирт из-за начала переписки: 1.0 в самом начале, плавно до 0 к EARLY_MESSAGES своим сообщениям.
    Если она отвечает сухо (low), усиления нет: ответа на флирт нет, снижаем сразу и дальше работают обычные правила уровня."""
    if our_count is None or level == "low":
        return 0.0
    return max(0.0, 1.0 - our_count / EARLY_MESSAGES)


def pick_tone(rng=random, level="mid", boost=0.0):
    base = TONE_WEIGHTS.get(level, TONE_WEIGHTS["mid"])
    weights = [b + (e - b) * boost for b, e in zip(base, EARLY_WEIGHTS)]
    return rng.choices(TONES, weights=weights, k=1)[0]


def tone_instruction(tone, rng=random):
    s = f"ТОН ЭТОГО ОТВЕТА (выбран случайно, держись его): {tone.instruction}"
    if tone.name in ("light", "playful"):
        if rng.random() < 0.4:
            s += " Последнее сообщение закончи символом «)» (так люди смягчают флирт), без пробела перед ним."
        else:
            s += " В этот раз без «)»."
    return s


_LEVEL_NOTES = {
    "high": "\nОНА ПРОЯВЛЯЕТ ИНИЦИАТИВУ И ИНТЕРЕС (спрашивает о тебе, пишет развёрнуто, шутит). Не сливайся: отвечай заинтересованно, "
            "поддержи и усиль флирт, не отвечай сухо.",
    "low": "\nОна отвечает сухо и коротко: не дави, не вытягивай её вопросами и не форсируй флирт.",
}


def style_prompt(persona, shape, tone, level="mid", drive=False):
    """Системный промпт для одного ответа: легенда, форма ответа, тон и пометка о том, как ведёт себя она."""
    return (persona + "\n\n" + shape_instruction(shape) + "\n" + tone_instruction(tone) + _LEVEL_NOTES.get(level, "")
            + (DRIVE_NOTE if drive else ""))


def build_style(persona, recent_out, her_texts, rng=random, our_count=None):
    """Всё для одного ответа: (промпт, форма, тон, уровень интереса).
    our_count: сколько своих сообщений уже в чате (для усиленного флирта в начале переписки)."""
    level = engagement(her_texts)
    drive = our_count is not None and our_count < DRIVE_MESSAGES
    shape = pick_shape(recent_out, rng, level, drive)
    tone = pick_tone(rng, level, warmth(our_count, level))
    return style_prompt(persona, shape, tone, level, drive), shape, tone, level


def shape_instruction(shape):
    return f"ФОРМА ЭТОГО ОТВЕТА (выбрана случайно для естественности, держись её): {shape.instruction}"


def enforce(texts, shape):
    """Подрезает ответ модели до формы: число сообщений и вопросы."""
    texts = [t.strip() for t in texts if isinstance(t, str) and t.strip()]
    if not texts:
        return []
    if not shape.allow_question:
        statements = [t for t in texts if not is_question(t)]
        texts = statements if statements else [re.sub(r"\?+", "", texts[0]).strip()]
    return texts[: shape.max_messages]


def recent_outgoing(hist, n):
    """Тексты последних n наших сообщений в чате (старые раньше)."""
    out = []
    for m in hist:
        if m.get("out"):
            text = re.sub(r"\s*\n?\s*\d{1,2}:\d{2}\s*$", "", m.get("text") or "").strip()
            if text:
                out.append(text)
    return out[-n:]


# ---------- усталость темы: не доить одну тему бесконечно ----------
TOPICS = {
    "сон и сны": r"\bсн(?:ы|ов|ам|ами|ах|а|у|е|ом)\b|\bсон\b|снит|приснил|присн|сплю|спать|спал|выспа|засыпа|просн|кошмар|бессонн|разбуд",
    "еда": r"\bеда\b|еду\b|поесть|поел|голод|завтрак|обед|ужин|пицц|бургер|суши|кофе|чай\b|готов(?:ить|лю|ишь)|вкусн",
    "работа": r"работ|смен[аыуе]\b|начальник|коллег|офис|зарплат",
    "учёба": r"учёб|учеб|универ|колледж|пар[аыуе]\b|экзамен|зачёт|зачет|сесси|препод",
    "погода": r"погод|дожд|снег\b|холодн|жарк|тепл[оа]\b|ветер|мороз",
    "музыка": r"музык|песн|трек|альбом|рэп|рок\b|группа\b|слушаешь|плейлист",
    "игры": r"\bигр[аыуе]\b|игра(?:ть|ешь|ю)|dota|дота|\bcs\b|кс\b|steam|стим",
    "кино и сериалы": r"фильм|кино\b|сериал|аниме|смотр(?:ела|ел|ишь|ю)\b",
    "животные": r"кот\b|кошк|собак|пёс|пес\b|питомц|котик|щенок",
    "здоровье": r"болит|заболел|простуд|температур|врач|лечи|здоровь",
}
_STOP = set("это как что так там тут вот просто очень тоже если когда потом тебя тебе меня мне она они его ещё еще или чтобы только уже даже было есть будет всё все".split())
STALE_WINDOW = 8       # сколько последних сообщений смотрим (обе стороны)
STALE_SHARE = 5        # столько из них об одной теме = пора менять
STALE_OWN = 3          # или столько из наших последних четырёх


def stale_topic(texts_in_order, own_flags):
    """Если разговор застрял на одной теме, возвращает её название, иначе None.
    texts_in_order: тексты сообщений по порядку; own_flags: True для наших. Смотрим последние STALE_WINDOW сообщений."""
    items = [(t.lower(), o) for t, o in zip(texts_in_order, own_flags) if isinstance(t, str) and t.strip()][-STALE_WINDOW:]
    if len(items) < 4:
        return None
    for name, rx in TOPICS.items():
        pat = re.compile(rx, re.I)
        hits = [bool(pat.search(t)) for t, _ in items]
        own = [h for h, (_, o) in zip(hits, items) if o][-4:]
        if sum(hits) >= STALE_SHARE or (len(own) >= 4 and sum(own) >= STALE_OWN):
            return name
    # общий случай: одно и то же слово (основа из 5 букв) почти в каждом сообщении
    counts = {}
    for t, _ in items:
        for stem in {w[:5] for w in re.findall(r"[а-яё]{5,}", t) if w not in _STOP}:
            counts[stem] = counts.get(stem, 0) + 1
    stem, n = max(counts.items(), key=lambda kv: kv[1], default=("", 0))
    return f"«{stem}…»" if n >= STALE_SHARE else None


STALE_NOTE = ("\nТЕМА ЗАСТРЯЛА: разговор уже долго идёт про «{topic}». Больше НЕ задавай вопросов по этой теме и не развивай её. "
              "Ответь на её последнюю реплику коротко (одной фразой, можно с подколом) и сам переведи разговор на ДРУГУЮ тему, "
              "которая ещё не обсуждалась: планы на выходные, музыка, еда, игры, кино и сериалы, путешествия, питомцы, учёба или работа, "
              "что-то весёлое из её анкеты. Переход делай естественно (одним вопросом или историей о себе), не оглашай, что меняешь тему.")
