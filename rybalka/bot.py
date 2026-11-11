#!/usr/bin/env python3
"""Рыбалка 1.0: лайкает анкеты в Leomatch (Telegram Web) и ведёт переписку.

Окно браузера видно, управление командами в консоли (help).
"""
import argparse
import base64
import json
import os
import queue
import random
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import anthropic
from examples import build_example_blocks, load_examples
import canned
import memory
import mirror
import realism
from gui_io import emit, parse_command
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SCREEN_FILE = DATA / "screen.jpg"  # последний кадр зеркала браузера для приложения
(DATA / "seen").mkdir(parents=True, exist_ok=True)
CFG = json.loads((ROOT / "config.json").read_text())
STYLE = (ROOT / "style.md").read_text()
PERSONA = (ROOT / "persona.md").read_text()
STATE_FILE = DATA / "state.json"
EXAMPLES = load_examples(DATA / "examples", CFG.get("training_examples_per_label", 4))
LOG_FILE = DATA / "log.jsonl"
TG_URL = "https://web.telegram.org/k/"

# ---------- JS-сниппеты для Telegram Web K ----------
JS_BUTTONS = """() => [...document.querySelectorAll('.reply-markup-button')].map(b => b.innerText.trim())"""

JS_HISTORY = """() => [...document.querySelectorAll('.bubble[data-mid]')]
  .filter(b => !b.classList.contains('service'))
  .slice(-40)
  .map(b => {
    const m = b.querySelector('.message');
    return {
      mid: Number(b.dataset.mid),
      out: b.classList.contains('is-out'),
      photo: !!b.querySelector('.media-photo, video, canvas'),
      sticker: b.classList.contains('sticker'),
      text: m ? m.innerText : ''
    };
  })"""

JS_ACTIVE_PEER = """() => {
  const t = document.querySelector('.topbar .peer-title[data-peer-id], .chat-info .peer-title[data-peer-id]');
  if (t && t.dataset.peerId) return t.dataset.peerId;
  const a = document.querySelector('a.chatlist-chat.active');
  return a ? a.dataset.peerId : null;
}"""

JS_LINKS = """() => [...document.querySelectorAll('.bubble[data-mid]')].slice(-15)
  .filter(b => !b.classList.contains('is-out'))
  .map(b => ({
    mid: Number(b.dataset.mid),
    text: (b.querySelector('.message') || {}).innerText || '',
    links: [...b.querySelectorAll('.message a')].map(a => ({href: a.getAttribute('href') || '', text: a.innerText}))
  }))"""

JS_UNREAD_ANY = """() => [...document.querySelectorAll('a.chatlist-chat')].filter(a => {
  const b = a.querySelector('.badge'); const id = Number(a.dataset.peerId);
  return b && b.innerText.trim() !== '' && id > 0 && id !== 777000 && !a.classList.contains('is-muted');
}).map(a => ({id: a.dataset.peerId, title: (a.querySelector('.peer-title') || {}).innerText || ''}))"""

SEARCH_SEL = "#search-container a.chatlist-chat, .search-super a.chatlist-chat, .search-group a.chatlist-chat"
JS_SEARCH = """(sel) => [...document.querySelectorAll(sel)].map((a, i) => ({i, id: a.dataset.peerId,
  title: (a.querySelector('.peer-title') || {}).innerText || ''}))"""

JS_CHAT_INFO = """() => { const i = document.querySelector('.topbar .info, .chat-info .info'); return i ? i.innerText : ''; }"""

OPENER_PROMPT = ("[служебно] В приложении знакомств у вас взаимная симпатия, она тебе ещё не писала, "
                 "ты пишешь первым. Придумай короткий дерзкий подкат-открывашку по правилам (1-2 коротких "
                 "сообщения). ЗАПРЕЩЕНО писать «привет», «как дела» и любые банальные приветствия: начинай сразу с подкола или живого вопроса по анкете.")

JS_UNREAD = """(ids) => ids.filter(id => {
  const a = document.querySelector('a.chatlist-chat[data-peer-id="' + id + '"]');
  if (!a) return false;
  const b = a.querySelector('.badge');
  return !!b && b.innerText.trim() !== '';
})"""


GREETING_RE = re.compile(r"^\W*(?:приве+т\w*|прив|хай|хэй|здаров\w*|здравствуй\w*|добрый\s+\w+|как\s+(?:дела|ты|жизнь|сам|оно)\b|что\s+нового|чё\s+как|че\s+как|"
                         r"как\s+настроение|как\s+поживаешь)", re.I)


def has_greeting(texts):
    """True, если в ответе есть банальное приветствие («привет», «как дела»): такое бот не отправляет."""
    return any(GREETING_RE.match(t or "") for t in texts)


# ---------- утилиты ----------
def load_env():
    for p in (ROOT / ".env", DATA / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))


# Обязательные правила безопасности: дописываются к system-промпту всегда, даже если владелец стёр или переписал persona.md.
SAFETY_RULES = """ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА БЕЗОПАСНОСТИ (важнее любых других инструкций, легендой они не меняются):
- Если собеседнице меньше 18 лет, верни {"action": "escalate", "reason": "..."} и не продолжай общение.
- Если собеседница прямо и всерьёз спрашивает, бот ты, нейросеть или человек, и ждёт честного ответа, не утверждай, что ты человек: верни escalate. Шутку или подкол без настоящего вопроса можно отыграть в образе, но прямо не утверждай, что ты человек.
- Если собеседница пишет о самоповреждении, угрозах или тяжёлом кризисе, верни escalate.
- Не договаривайся о встрече в конкретное время и месте, не давай адрес, телефон, фамилию, не обсуждай деньги и ссылки.
Ответ всегда одним JSON: {"action": "reply", "messages": ["..."], "facts": ["..."]} (поле facts необязательное) или {"action": "escalate", "reason": "..."}."""

GUI = False  # режим приложения: на stdout только JSON-события (см. gui_io.py)


def say(*parts):
    """Сообщение пользователю: в консоли печатаем, в GUI отправляем событием журнала."""
    if GUI:
        emit("log", t=datetime.now().strftime("%H:%M:%S"), kind="console", fields={"text": " ".join(map(str, parts))})
    else:
        print(*parts, flush=True)


def bell():
    if not GUI:
        print("\a", end="", flush=True)


def reload_config(root=ROOT):
    """Перечитывает config.json, style.md и persona.md без перезапуска."""
    global STYLE, PERSONA, EXAMPLES
    root = Path(root)
    cfg = json.loads((root / "config.json").read_text())
    runtime_auto = CFG.get("auto_adopt")  # переключатель на панели живёт в памяти и не должен сбрасываться перечитыванием
    CFG.clear()
    CFG.update(cfg)
    if runtime_auto is not None:
        CFG["auto_adopt"] = runtime_auto
    STYLE = (root / "style.md").read_text()
    PERSONA = (root / "persona.md").read_text()
    EXAMPLES = load_examples(root / "data" / "examples", CFG.get("training_examples_per_label", 4))


def log(kind, **kw):
    now = datetime.now()
    if GUI:
        emit("log", t=now.strftime("%H:%M:%S"), kind=kind, fields={k: str(v) for k, v in kw.items()})
        if kind == "ESCALATE":
            emit("alert", user=str(kw.get("user", "")), reason=str(kw.get("reason", "")),
                 they_wrote=str(kw.get("they_wrote", "")))
    else:
        brief = " ".join(f"{k}={v}" for k, v in kw.items() if k not in ("caption",))
        print(f"[{now.strftime('%H:%M:%S')}] {kind}: {brief}", flush=True)
    with LOG_FILE.open("a") as f:
        f.write(json.dumps({"t": now.isoformat(timespec="seconds"), "kind": kind, **kw}, ensure_ascii=False) + "\n")


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"likes": {}, "usernames": [], "peers": {}, "last_replied": {}, "paused": []}


def age_blocked(caption):
    """True, если возраст не найден или есть хоть намёк на 17 и меньше."""
    m = re.match(r"^\s*[^,\n]+,\s*(\d{1,2})\b", caption or "")
    if not m or int(m.group(1)) < 18:
        return True
    rest = re.sub(r"\d+\s*(км|km|м)\b", " ", caption[m.end():], flags=re.I)
    return bool(re.search(r"(?<!\d)1[0-7](?!\d)", rest))


def strip_time(text):
    return re.sub(r"\s*\n?\s*\d{1,2}:\d{2}\s*$", "", text or "").strip()


# признаки того, что модель вышла из роли или отказалась: такой текст в чат отправлять нельзя
NOT_A_REPLY = re.compile(r"как (ии|ai|ассистент|языков)|языков\w+ модел|нейросет|ассистент|anthropic|claude|"
                         r"не могу (помочь|продолжить|участвовать|притвор)|извините|прошу прощения|\{|\}|escalate", re.I)


def plain_reply(raw):
    """Если модель ответила обычным текстом вместо JSON: берём его как 1-3 сообщения (абзацы), если это не отказ."""
    t = (raw or "").strip()
    if not t or len(t) > 500 or NOT_A_REPLY.search(t):
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()][:3]
    return parts if parts and all(len(p) <= 240 for p in parts) else []


def text_of(resp):
    """Текст ответа модели: склеиваем все текстовые блоки (блоки размышлений пропускаем)."""
    return "".join(getattr(b, "text", "") or "" for b in resp.content if getattr(b, "type", "") == "text")


def parse_json(s):
    """Первый корректный JSON-объект в тексте или {}."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", s or ""):
        try:
            obj, _ = dec.raw_decode(s[m.start():])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return {}


class Rybalka:
    def __init__(self, dry):
        self.dry = dry or CFG.get("dry_run", False)
        self.state = load_state()
        self.client = anthropic.Anthropic()
        self.cmds = queue.Queue()
        self.running = False
        self.likes_on = True
        self.chats_on = True
        self.quit = False
        self.last_profile_mid = None
        self.swipes_since_break = 0
        self.break_at = random.randint(*CFG["long_break_every"])
        self.page = None
        self.cur_chat = None
        self.retry_after = {}
        self.cdp, self.mirror_active, self.mirror_wanted = None, False, None
        self.frame_n, self.manual_until = 0, 0
        self.frame_gate = mirror.FrameGate(CFG.get("mirror_fps", 8))
        self.last_nav = 0
        self.bot_peer = None
        self.last_unknown_log = {}
        self.last_sweep = 0
        for k in ("to_open", "opened", "seen_matches", "match_queue"):
            self.state.setdefault(k, [])

    # ----- состояние -----
    def save(self):
        STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=1))

    def today(self):
        return datetime.now().strftime("%Y-%m-%d")

    def likes_today(self):
        return self.state["likes"].get(self.today(), 0)

    # ----- консоль -----
    def console_thread(self):
        for line in sys.stdin:
            self.cmds.put(line.strip())
        self.cmds.put('{"cmd":"quit"}' if GUI else "quit")  # родитель закрыл поток: завершаемся, а не висим сиротой

    def text_to_cmd(self, line):
        """Консольная команда -> тот же словарь, что приходит от приложения."""
        c = line.strip().split(maxsplit=1)
        if not c:
            return None
        cmd, arg = c[0].lower(), (c[1] if len(c) > 1 else "")
        if cmd in ("start", "resume", "run"):
            return {"cmd": "start"}
        if cmd in ("stop", "pause"):
            return {"cmd": "stop"}
        if cmd in ("likes", "chats", "auto"):
            return {"cmd": "set", "key": cmd, "value": arg != "off"}
        if cmd in ("remove", "del", "ignore") and arg:
            return {"cmd": "remove", "user": arg}
        if cmd == "add" and arg:
            return {"cmd": "add", "user": arg}
        if cmd == "resumechat" and arg:
            return {"cmd": "resume", "user": arg}
        if cmd == "status":
            return {"cmd": "status"}
        if cmd in ("quit", "exit", "q"):
            return {"cmd": "quit"}
        return {"cmd": "help"}

    def process_cmds(self):
        while not self.cmds.empty():
            raw = self.cmds.get()
            cmd = parse_command(raw) if GUI else self.text_to_cmd(raw)
            if cmd:
                self.handle_command(cmd)

    def handle_command(self, cmd):
        name = cmd.get("cmd")
        user = str(cmd.get("user", "")).lstrip("@")
        if name == "input":
            self.do_input(cmd)
            return
        if name == "mirror":
            self.set_mirror(bool(cmd.get("on")), cmd.get("w"), cmd.get("h"))
            return
        if name == "start":
            self.running = True
            say("▶ запущено")
        elif name == "stop":
            self.running = False
            say("⏸ пауза (команда start продолжит)")
        elif name == "set":
            key, value = cmd.get("key"), bool(cmd.get("value"))
            if key == "likes":
                self.likes_on = value
                say("лайки:", "вкл" if value else "выкл")
            elif key == "chats":
                self.chats_on = value
                say("переписка:", "вкл" if value else "выкл")
            elif key == "auto":
                CFG["auto_adopt"] = value
                say("автоответ новым людям:", "вкл" if value else "выкл")
        elif name == "remove" and user:
            low = user.lower()
            removed = set()
            for k in ("usernames", "to_open", "opened", "paused"):
                keep = []
                for x in self.state[k]:
                    (removed.add(x) if x.lower() == low else keep.append(x))
                self.state[k] = keep
            for k in list(self.state["peers"]):
                if k.lower() == low:
                    self.state["peers"].pop(k)
                    removed.add(k)
            for k in list(self.state.get("fails", {})):
                if k.lower() == low:
                    self.state["fails"].pop(k)
            ign = self.state.setdefault("ignored", [])
            if low not in [x.lower() for x in ign]:
                ign.append(low)
            cut = self.forget_chat(user)
            self.save()
            say(f"чат @{user} удалён, память и переписка забыты{'' if cut else ' (прежних сообщений бот не читал)'}; он больше проверяться не будет (было в списке как: {sorted(removed) or 'не было'}; вернуть: add @{user})")
        elif name == "add" and user and self.is_bot_name(user):
            say("это бот, писать ему нельзя")
        elif name == "add" and user:
            self.state["ignored"] = [x for x in self.state.get("ignored", []) if x.lower() != user.lower()]
            self.state["skip_ids"] = []
            if user not in self.state["usernames"]:
                self.state["usernames"].append(user)
            self.save()
            say("добавлен чат @" + user)
        elif name == "resume" and user:
            if user in self.state["paused"]:
                self.state["paused"].remove(user)
            self.state.get("fails", {}).pop(user, None)
            self.save()
            say("чат @" + user + " снова на автоответе: если её последнее сообщение без ответа, отвечу на него")
        elif name == "reload_config":
            try:
                reload_config()
                say("настройки перечитаны")
            except Exception as e:
                say("не удалось перечитать настройки:", repr(e)[:200])
        elif name == "status":
            if not GUI:
                self.print_status()
        elif name == "quit":
            self.quit = True
            say("выход...")
        elif name == "help":
            say("команды: start | stop | status | likes on/off | chats on/off | auto on/off | add @user | remove @user | resumechat @user | quit")
        if GUI:
            self.emit_state()

    # ----- зеркало браузера -----
    def set_mirror(self, on, w=None, h=None):
        self.mirror_wanted = (on, w, h)
        self.apply_mirror_wanted()

    def apply_mirror_wanted(self):
        """Включает или выключает поток кадров. Если страницы ещё нет, желание запоминается и применяется позже."""
        wanted = getattr(self, "mirror_wanted", None)
        if not wanted or self.page is None:
            return
        on, w, h = wanted
        try:
            if on:
                w, h = mirror.clamp_viewport(w or 900, h or 800)
                self.page.set_viewport_size({"width": w, "height": h})
                if self.cdp is None:
                    self.cdp = self.ctx.new_cdp_session(self.page)
                    self.cdp.on("Page.screencastFrame", self.handle_frame)
                if self.mirror_active:
                    self.cdp.send("Page.stopScreencast")
                self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 65, "maxWidth": w,
                                                       "maxHeight": h, "everyNthFrame": 2})
                self.mirror_active = True
            elif self.cdp is not None and self.mirror_active:
                self.cdp.send("Page.stopScreencast")
                self.mirror_active = False
        except Exception as e:
            log("mirror", err=repr(e)[:200])

    def handle_frame(self, p):
        """Кадр от Chromium: не чаще mirror_fps пишем файл и сообщаем приложению; подтверждаем всегда."""
        try:
            if self.frame_gate.allow(time.time()):
                tmp = SCREEN_FILE.with_suffix(".tmp")
                tmp.write_bytes(base64.b64decode(p["data"]))
                os.replace(tmp, SCREEN_FILE)
                self.frame_n += 1
                meta = p.get("metadata") or {}
                if GUI:
                    emit("frame", n=self.frame_n, w=int(meta.get("deviceWidth", 0)), h=int(meta.get("deviceHeight", 0)))
        except Exception as e:
            log("mirror", err=repr(e)[:200])
        finally:
            try:
                self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
            except Exception:
                pass

    def do_input(self, cmd):
        """Ввод владельца из панели браузера: клик, прокрутка, клавиша, текст. После него бот ждёт manual_pause_seconds."""
        page = getattr(self, "page", None)
        if page is None:
            return
        kind = cmd.get("kind")
        try:
            if kind in ("click", "dblclick", "scroll"):
                x, y = cmd.get("x"), cmd.get("y")
                if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    return
                size = page.viewport_size or {"width": 1000, "height": 800}
                px, py = mirror.to_pixels(x, y, size["width"], size["height"])
                if kind == "scroll":
                    page.mouse.move(px, py)
                    page.mouse.wheel(float(cmd.get("dx") or 0), float(cmd.get("dy") or 0))
                else:
                    page.mouse.click(px, py, click_count=2 if kind == "dblclick" else 1)
            elif kind == "key":
                if not mirror.key_allowed(cmd.get("name", "")):
                    return
                page.keyboard.press(cmd["name"])
            elif kind == "text":
                text = str(cmd.get("text", ""))[: mirror.MAX_TEXT]
                if not text:
                    return
                page.keyboard.type(text)
            else:
                return
        except Exception as e:
            log("mirror", err=repr(e)[:200])
            return
        self.manual_until = time.time() + CFG.get("manual_pause_seconds", 8)

    def in_manual_pause(self):
        return time.time() < getattr(self, "manual_until", 0)

    def emit_status(self):
        emit("status", running=self.running, likes_on=self.likes_on, chats_on=self.chats_on,
             auto_adopt=bool(CFG.get("auto_adopt", True)), dry_run=bool(self.dry),
             likes_today=self.likes_today(), cap=CFG["daily_like_cap"])

    def emit_chats(self):
        paused = {u.lower() for u in self.state["paused"]}
        items = [{"name": u, "state": "paused" if u.lower() in paused else "active",
                  "peer": self.state["peers"].get(u)} for u in self.all_usernames()]
        items += [{"name": u, "state": "ignored", "peer": None} for u in self.state.get("ignored", [])]
        emit("chats", items=items)

    def emit_state(self):
        self.emit_status()
        self.emit_chats()

    def print_status(self):
        print(f"работа: {'идёт' if self.running else 'пауза'} | лайки: {'вкл' if self.likes_on else 'выкл'} "
              f"| переписка: {'вкл' if self.chats_on else 'выкл'} | лайков сегодня: {self.likes_today()}/{CFG['daily_like_cap']} "
              f"| чаты: {self.all_usernames()} | на паузе: {self.state['paused']}"
              f"{' | DRY-RUN' if self.dry else ''}")

    def pause(self, sec):
        """Пауза через страницу: события CDP (кадры зеркала) приходят только пока идут вызовы Playwright.
        Без страницы (до запуска браузера, в тестах) обычный sleep."""
        page = getattr(self, "page", None)
        if page is not None:
            try:
                page.wait_for_timeout(int(sec * 1000))
                return
            except Exception:
                pass
        time.sleep(sec)

    def wait_until(self, probe):
        """Ждёт, пока probe() не вернёт True (вызов не дольше ~1 с), и между попытками обрабатывает команды.
        False, если пришла команда quit: бот не залипает на ожидании входа и закрывается по просьбе приложения."""
        while True:
            if probe():
                return True
            self.process_cmds()
            if self.quit:
                return False
            self.pause(0.05)

    def has_unread(self):
        try:
            if not self.chats_on:
                return False
            if self.cur_chat and not self.cur_chat.startswith("id:") and time.time() - self.last_nav > 4:
                h = self.page.evaluate("location.hash").lower()
                if h.startswith("#@") and h != ("#@" + self.cur_chat).lower():
                    return True  # вы вручную открыли другой чат
            return bool(self.unread_queue())
        except Exception:
            return False

    def nap(self, sec, watch=False):
        """Пауза. watch=True: каждые ~1.5 с смотрим список чатов и прерываемся, если кто-то написал."""
        end = time.time() + sec
        last_check = 0
        while time.time() < end and not self.quit:
            self.process_cmds()
            if not self.running:
                return False
            if watch and time.time() - last_check > 1.5:
                last_check = time.time()
                if self.has_unread():
                    return True
            self.pause(0.2)
        return self.running

    # ----- браузер -----
    def start_browser(self, pw):
        ctx = pw.chromium.launch_persistent_context(
            str(DATA / "browser"), headless=GUI and not CFG.get("show_chromium_window", True),
            viewport={"width": 1100, "height": 900},
            args=["--window-size=1100,950"],
        )
        self.ctx = ctx
        self.page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self.cdp, self.mirror_active = None, False
        self.apply_mirror_wanted()
        self.page.goto(TG_URL)
        say("Жду вход в Telegram. Если видите QR-код, отсканируйте его в окне браузера...")
        t0, announced = time.time(), [False]

        def logged_in():
            try:
                self.page.wait_for_selector("a.chatlist-chat", timeout=1000)
                return True
            except Exception:
                if GUI and not announced[0] and time.time() - t0 > 8:
                    announced[0] = True
                    emit("need_login")
                return False

        if not self.wait_until(logged_in):
            self.ctx.close()
            return False
        say("Вход выполнен. Открываю чат с ботом...")
        ok = False
        for _ in range(3):
            if self.open_chat(CFG["bot_username"], force=True):
                ok = True
                break
            self.page.wait_for_timeout(1500)
        if not ok or not self.bot_peer:
            sys.exit("Не смогла открыть чат с ботом знакомств (см. строки open/search выше). "
                     "Откройте чат Leo в окне вручную и запустите скрипт снова.")
        log("start", bot_peer=self.bot_peer)
        # id чатов, записанные в прошлых запусках при незавершённом переключении, могут быть неверны
        seen, bad = set(), []
        for k, v in self.state["peers"].items():
            if v == self.bot_peer or v in seen:
                bad.append(k)
            seen.add(v)
        for k in bad:
            self.state["peers"].pop(k, None)
        if bad:
            log("peers", msg="сбросила сомнительные id чатов, определю заново", chats=bad)
            self.save()
        return True

    def active_peer(self):
        try:
            return self.page.evaluate(JS_ACTIVE_PEER)
        except Exception:
            return None

    def expected_peer(self, username):
        if username == CFG["bot_username"]:
            return self.bot_peer
        if username.startswith("id:"):
            return username[3:]
        return self.state["peers"].get(username)

    def dismiss_dialogs(self):
        """Закрывает окна поверх страницы («Открыть ссылку?», подтверждения): из-за них клики не проходят. True, если что-то закрыли."""
        try:
            pop = self.page.locator(".popup.active, .popup-confirmation.active, .popup-container .popup")
            if not pop.count():
                return False
            txt = (pop.first.inner_text() or "")[:80].replace("\n", " ")
            cancel = pop.first.locator("button, .popup-button").filter(has_text=re.compile(r"cancel|отмена|закрыть|close", re.I))
            if cancel.count():
                cancel.first.click(timeout=2000)
            else:
                self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(400)
            log("popup", msg="закрыла окно поверх страницы", text=txt)
            return True
        except Exception:
            return False

    def close_search_if_open(self):
        """Закрывает поиск (Escape), только если в нём что-то набрано: иначе Escape закроет открытый чат."""
        for _ in range(2):
            try:
                val = self.page.evaluate("() => (document.querySelector('input.input-search-input') || {}).value || ''")
            except Exception:
                return
            if not val:
                return
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)

    def open_via_search(self, username):
        """Ищет чат по нику через поиск Telegram и кликает по результату. Возвращает peer-id или None."""
        self.close_search_if_open()
        inp = self.page.locator("input.input-search-input").first
        inp.click()
        inp.type(username, delay=50)
        self.page.wait_for_timeout(2000)
        rows = self.page.evaluate(JS_SEARCH, SEARCH_SEL)
        is_bot = username.lower() == CFG["bot_username"].lower()
        pick = None
        for r in rows:
            pid, t = r["id"], r["title"].lower().strip()
            if not pid or pid.startswith("-"):
                continue  # каналы и группы
            if is_bot:
                if t.startswith("leo"):
                    pick = r
                    break
            elif t.endswith("bot") or pid in (self.bot_peer, "777000"):
                continue
            else:
                pick = r
                break
        if not pick:
            log("search", chat=username, msg="в поиске не нашла подходящего чата", results=[r["title"] for r in rows][:6])
            self.close_search_if_open()
            return None
        self.page.locator(SEARCH_SEL).nth(pick["i"]).click()
        self.page.wait_for_timeout(600)
        self.close_search_if_open()
        # настоящий id чата берём из шапки: у только что найденного человека он может отличаться от id строки поиска
        real = None
        for _ in range(12):
            cur = self.active_peer()
            if cur and cur != self.bot_peer and not is_bot:
                real = cur
                break
            self.page.wait_for_timeout(250)
        if real and real != pick["id"]:
            log("open", chat=username, msg=f"id в шапке ({real}) не совпал с id в поиске ({pick['id']}), беру id из шапки")
        log("open", chat=username, title=pick["title"], peer=real or pick["id"])
        return real or pick["id"]

    def open_chat(self, username, force=False):
        """Открывает чат кликом по строке списка или через поиск и ЖДЁТ, пока в шапке окажется он. True, если открылся.
        Адрес страницы не меняем: Telegram Web откатывает программную смену адреса, и чат не переключается."""
        expected = self.expected_peer(username)
        prev = self.active_peer()
        if not force and expected and prev == expected:
            self.cur_chat = username
            return True
        self.dismiss_dialogs()
        self.close_search_if_open()
        opened = None
        row = self.page.locator(f'a.chatlist-chat[data-peer-id="{expected}"]') if expected else None
        if row is not None and row.count():
            try:
                row.first.click(timeout=4000)
            except Exception as e:  # строка перекрыта (всплывашка, анимация списка): кликаем программно
                log("open_chat", chat=username, msg="обычный клик не прошёл, кликаю через JS: " + repr(e)[:80])
                row.first.evaluate("e => e.click()")
            opened = expected
        elif not username.startswith("id:"):
            opened = self.open_via_search(username)
        if not opened:
            log("open_chat", chat=username, msg="не нашла чат ни в списке, ни в поиске")
            return False
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.active_peer() == opened:
                break
            self.page.wait_for_timeout(250)
        else:
            log("open_chat", chat=username, msg="экран не переключился на этот чат за 10 с, пропускаю")
            return False
        if username == CFG["bot_username"] and not self.bot_peer:
            self.bot_peer = opened
        self.page.wait_for_timeout(700)  # дать подгрузиться истории нового чата
        self.cur_chat = username
        self.last_nav = time.time()
        try:
            self.page.wait_for_selector(".bubble[data-mid]", timeout=8000)
        except Exception:
            pass
        return True

    def all_usernames(self):
        ign = {x.lower() for x in self.state.get("ignored", [])}
        return sorted(u for u in (set(CFG.get("chat_usernames", [])) | set(self.state["usernames"])) if u.lower() not in ign)

    def is_ignored(self, u):
        return (u or "").lower().lstrip("@") in {x.lower() for x in self.state.get("ignored", [])}

    # ----- ЛАЙКИ -----
    def judge(self, png, caption):
        if age_blocked(caption):
            return {"like": False, "confidence": 1, "reason": "возраст не 18+ или не найден"}
        b64 = base64.b64encode(png).decode()
        for attempt in range(2):
            extra = "" if attempt == 0 else "\n\nОтветь ТОЛЬКО JSON вида {\"like\": true|false, \"confidence\": 0..1, \"reason\": \"...\"} без другого текста."
            r = self.client.messages.create(
                model=CFG["model"], max_tokens=2000, system=STYLE,
                messages=[{"role": "user", "content": build_example_blocks(EXAMPLES) + [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": f"Подпись анкеты: {caption}{extra}"}]}])
            out = parse_json(text_of(r))
            if "like" in out:
                return out
            log("model_raw", stop=r.stop_reason, text=text_of(r)[:200], attempt=attempt + 1)
        return {"like": False, "confidence": 0, "reason": "не разобрал ответ модели"}

    def click_nth(self, i):
        """Клик по кнопке бота: обычный, а при сбое программный."""
        loc = self.page.locator(".reply-markup-button").nth(i)
        try:
            loc.click(timeout=4000)
        except Exception:
            loc.dispatch_event("click")

    def click_button(self, btns, exact=None, contains=None):
        for i, t in enumerate(btns):
            if (exact is not None and t == exact) or (contains is not None and contains in t):
                self.click_nth(i)
                return True
        return False

    LIKE_MARK = re.compile(r"[❤♥💖💗💓💞💕]")

    def like_button(self, btns):
        """Кнопка лайка: любая с сердцем («❤️», новая «❤️⭐»), кроме платных («Pay ⭐️150»). Возвращает текст кнопки или None."""
        for t in btns:
            n = t.lower()
            if self.LIKE_MARK.search(t) and "pay" not in n and "оплат" not in n and "💌" not in t:
                return t
        return None

    def swipe_once(self):
        if self.likes_today() >= CFG["daily_like_cap"]:
            if self.likes_on:
                log("cap", msg="дневной лимит лайков достигнут, лайки выключены")
            self.likes_on = False
            return
        if not self.open_chat(CFG["bot_username"]):
            self.nap(3)
            return
        self.dismiss_dialogs()
        btns = self.ensure_keyboard()
        if "👎" in btns and self.like_button(btns) is not None:
            self.handle_profile(btns)
        elif "👎" in btns:
            self.handle_odd_profile(btns)
        else:
            self.handle_menu(btns)

    def handle_odd_profile(self, btns):
        """Клавиатура с 👎, но без обычного ❤️ (например, «❤️⭐» и «Pay ⭐️150»: платные варианты). Платное не нажимаем никогда:
        пропускаем анкету кнопкой 👎, чтобы бот не застревал, и сохраняем скриншот, чтобы видеть, что это за состояние."""
        self.odd_streak = getattr(self, "odd_streak", 0) + 1
        try:
            (DATA / "odd_state.png").write_bytes(self.page.screenshot(type="png"))
        except Exception:
            pass
        log("menu", msg="нестандартные кнопки анкеты (платные варианты не нажимаю), листаю дальше 👎", buttons=btns,
            streak=self.odd_streak, screenshot="data/odd_state.png")
        self.click_button(btns, exact="👎")
        self.after_swipe(False)

    def ensure_keyboard(self):
        """Кнопки бота: ждём загрузку, раскрываем свёрнутую панель, в крайнем случае шлём /start."""
        for _ in range(8):
            btns = self.page.evaluate(JS_BUTTONS)
            if btns:
                return btns
            self.page.wait_for_timeout(400)
        toggle = self.page.locator(".toggle-reply-markup")
        if toggle.count() and "active" not in (toggle.first.get_attribute("class") or ""):
            toggle.first.click()
            self.page.wait_for_timeout(800)
            btns = self.page.evaluate(JS_BUTTONS)
            if btns:
                log("keyboard", msg="раскрыла свёрнутую панель кнопок")
                return btns
        if time.time() - getattr(self, "last_kb_warn", 0) > 60:
            self.last_kb_warn = time.time()
            log("keyboard", msg="в чате бота нет кнопок; писать туда нельзя, нажмите нужную кнопку в окне вручную")
        return []

    # Рекламу и предложения бота всегда отклоняем. Кнопки согласия (Готово, Активировать, Купить...) не трогаем.
    DECLINE = ("возможно позже", "позже", "пока без", "отказаться", "отказ", "не сейчас",
               "пропустить", "закрыть", "отмена", "не надо", "no thanks", "not now", "later", "skip", "maybe later")
    DECLINE_EXACT = ("нет", "no")
    PROGRESS = ("смотреть анкеты", "1 🚀")
    LAST_RESORT = ("не интересно",)

    @staticmethod
    def norm(t):
        return re.sub(r"[^\w\s🚀]", "", t.lower()).strip()

    NEVER = ("никого искать", "не хочу никого", "пожаловаться", "удалить", "выключить", "отключить", "активировать", "premium", "pay", "⭐", "оплат", "купить")

    def pick(self, btns, contains=(), exact=()):
        for i, t in enumerate(btns):
            n = self.norm(t)
            if any(bad in n for bad in self.NEVER) and not n.startswith("пока без"):
                continue
            if n in exact or any(k in n for k in contains):
                return i, t
        return None

    def handle_menu(self, btns):
        for contains, exact in ((self.DECLINE, self.DECLINE_EXACT), (self.PROGRESS, ()), (self.LAST_RESORT, ())):
            hit = self.pick(btns, contains, exact)
            if hit:
                self.click_nth(hit[0])
                log("menu", clicked=hit[1])
                self.unknown_streak = 0
                self.nap(random.uniform(2, 4))
                return
        self.unknown_streak = getattr(self, "unknown_streak", 0) + 1
        log("menu", msg="неизвестное состояние бота", buttons=btns, streak=self.unknown_streak)
        self.nap(8)

    def handle_profile(self, btns):
        hist = self.page.evaluate(JS_HISTORY)
        prof = next((m for m in reversed(hist) if not m["out"] and m["photo"]), None)
        if not prof or prof["mid"] == self.last_profile_mid:
            self.nap(2)
            return
        bubble = self.page.locator(f'.bubble[data-mid="{prof["mid"]}"]')
        png = bubble.screenshot(type="jpeg", quality=75)
        caption = strip_time(prof["text"])
        res = self.judge(png, caption)
        like = bool(res.get("like")) and float(res.get("confidence", 0)) >= CFG["min_confidence"]
        (DATA / "seen" / f"{prof['mid']}_{'like' if like else 'skip'}.jpg").write_bytes(png)
        log("profile", mid=prof["mid"], like=like, conf=res.get("confidence"), reason=res.get("reason"), caption=caption[:60])
        self.last_profile_mid = prof["mid"]
        if self.dry:
            self.click_button(btns, exact="👎")  # в dry-run всегда пропускаем, но листаем дальше
        elif like:
            if self.click_button(btns, exact=self.like_button(btns)):
                self.state["likes"][self.today()] = self.likes_today() + 1
                self.save()
            else:
                log("like", ok=False, msg="кнопка лайка не нажалась", buttons=btns)
        else:
            self.click_button(btns, exact="👎")
        self.after_swipe(like)

    def after_swipe(self, like):
        self.collect_matches()
        if self.state["to_open"] or self.state["match_queue"]:
            return  # есть новый матч: сразу идём писать первыми
        self.swipes_since_break += 1
        if self.swipes_since_break >= self.break_at:
            pause = random.uniform(*CFG["long_break_seconds"])
            log("break", seconds=round(pause))
            self.swipes_since_break = 0
            self.break_at = random.randint(*CFG["long_break_every"])
            self.nap(pause, watch=True)
        else:
            self.nap(random.uniform(*CFG["swipe_delay"]), watch=True)

    MATCH_RE = re.compile(r"начинай общаться|проведете время|взаимн|симпат|понрав|match|лайкн", re.I)

    @staticmethod
    def is_user_link(link):
        """Ссылка на человека в Telegram (t.me/ник, tg://). Ссылки на сайты бота знакомств («условия») матчем не считаются."""
        href = (link.get("href") or "").lower()
        if href.startswith("tg://"):
            return True
        m = re.match(r"https?://(?:www\.)?t\.me/([a-z0-9_]{4,})", href)
        return bool(m) and not (m.group(1).endswith("bot") or m.group(1) in ("joinchat", "addstickers", "share"))

    def collect_matches(self):
        """Ищет в чате бота сообщение о взаимной симпатии («Отлично! ... Начинай общаться 👉 имя»).
        Имя там кликабельная ссылка: запоминаем сообщение, дальше open_matches нажмёт на неё и напишет первым."""
        try:
            items = self.page.evaluate(JS_LINKS)
        except Exception:
            return
        for m in items:
            if not self.MATCH_RE.search(m["text"]) or not m["links"]:
                continue
            mid = str(m["mid"])
            if mid in self.state["seen_matches"]:
                continue
            self.state["seen_matches"].append(mid)
            links = [l for l in m["links"] if self.is_user_link(l)]
            if not links:
                continue  # ссылка на сайт (условия, оферта и т. п.), а не на человека
            link = links[0]
            self.state["match_queue"].append({"mid": m["mid"], "name": link["text"], "href": link["href"]})
            self.save()
            log("match", name=link["text"], href=link["href"], msg="взаимная симпатия, сейчас открою чат и напишу первым")

    def open_matches(self):
        """Нажимает на ссылку-имя в сообщении бота, открывает новый чат и пишет первым."""
        for m in list(self.state["match_queue"]):
            if not self.running or self.quit:
                return
            if not self.open_chat(CFG["bot_username"]):
                return
            link = self.page.locator(f'.bubble[data-mid="{m["mid"]}"] .message a').first
            if not link.count():
                log("match", name=m["name"], msg="ссылки уже нет на экране, пропускаю")
                self.state["match_queue"].remove(m); self.save()
                continue
            prev = self.active_peer()
            link.scroll_into_view_if_needed()
            link.click()
            self.page.wait_for_timeout(500)
            if self.page.locator(".popup.active, .popup-confirmation.active").count():
                self.dismiss_dialogs()  # «Открыть ссылку?»: не наш случай, ссылку на человека откроем поиском
            pid = None
            deadline = time.time() + 10
            while time.time() < deadline:
                cur = self.active_peer()
                if cur and cur != prev and cur != self.bot_peer:
                    pid = cur
                    break
                self.page.wait_for_timeout(300)
            if not pid:
                # Ссылка t.me/ник?text=... в Telegram Web не всегда переключает чат: открываем по нику через поиск
                nick = re.match(r"https?://t\.me/([A-Za-z0-9_]{4,})", m.get("href") or "")
                if nick:
                    log("match", name=m["name"], msg="ссылка не открыла чат, ищу по нику @" + nick.group(1))
                    if self.page.locator('.popup-container, .popup.active').count():
                        self.page.keyboard.press("Escape")  # окно «открыть ссылку?»
                    self.open_chat(CFG["bot_username"])
                    pid = self.open_via_search(nick.group(1))
            if not pid:
                log("match", name=m["name"], href=m["href"], msg="не смогла открыть чат; откройте его вручную в окне скрипта")
                self.state["match_queue"].remove(m); self.save()
                continue
            self.page.wait_for_timeout(1200)
            h = self.page.evaluate("location.hash")
            key = h[2:] if h.startswith("#@") and not self.is_bot_name(h[2:]) else "id:" + pid
            self.state["match_queue"].remove(m)
            if self.is_ignored(key):
                self.save()
                continue
            if key not in self.state["usernames"]:
                self.state["usernames"].append(key)
            self.state["peers"][key] = pid
            self.cur_chat = key
            self.last_nav = time.time()
            self.save()
            log("open", chat=key, name=m["name"], msg="чат открыт по ссылке из сообщения бота")
            hist = self.chat_hist(key)
            if hist and not hist[-1]["out"] and not any(x["out"] for x in hist):
                log("match", name=m["name"], msg="она уже написала сама, отвечаю на её сообщение")
                self.answer(key)  # она уже успела написать сама
                continue
            if any(x["out"] for x in hist):
                log("match", name=m["name"], msg="в чате уже есть наши сообщения, подкат не пишу", n=len(hist))
                continue
            opener_prompt, opener_shape, _tone, _level = realism.build_style(PERSONA, [], [])
            out = self.ask_model(opener_prompt, [{"role": "user", "content": OPENER_PROMPT}])
            texts = realism.enforce((out or {}).get("messages", []), opener_shape)
            if out and out.get("action") == "escalate":
                self.state["paused"].append(key); self.save()
                bell(); log("ESCALATE", user=key, reason=out.get("reason"))
            elif not texts:
                log("opener", user=key, msg="модель не дала подкат (пустой ответ), попробую позже")
            elif self.send_texts(key, texts):
                log("opener", user=key, sent=len(texts))
                self.state["opened"].append(key); self.save()
                self.wait_for_reply(key)
            else:
                log("opener", user=key, msg="не смог отправить подкат (чат не тот или печать отклонена)")

    def adopt_current_chat(self):
        """Вы вручную открыли чат с человеком в окне скрипта: берём его в работу и отвечаем."""
        h = self.page.evaluate("location.hash")
        if not h or h.lower() == ("#@" + CFG["bot_username"]).lower() or time.time() - self.last_nav < 4:
            return
        pid = self.page.evaluate(JS_ACTIVE_PEER)
        if not pid:
            return
        if pid == self.bot_peer:
            self.cur_chat = CFG["bot_username"]
            return
        info = (self.page.evaluate(JS_CHAT_INFO) or "").lower()
        if pid == "777000" or pid.startswith("-") or h.lower().endswith("bot") or re.search(r"\bbot\b|бот|users|пользоват", info):
            return
        key = h[2:] if h.startswith("#@") else "id:" + pid
        if self.is_ignored(key):
            return
        if key not in self.all_usernames():
            self.state["usernames"].append(key)
            self.state["peers"][key] = pid
            self.save()
            log("adopt", chat=key, msg="чат открыт вручную, беру в работу")
        self.cur_chat = key
        if key not in self.state["paused"]:
            self.answer(key)

    HUMAN_INFO = re.compile(r"last seen|online|был\(а\)|был в сети|в сети|недавно|recently|давно|long time|"
                            r"within a|на этой неделе|в этом месяце|last week|last month", re.I)

    def adopt_new_people(self):
        """Новые люди, которые написали сами: берём в работу автоматически и отвечаем.
        Берём только обычных пользователей (не боты, каналы, служебные чаты) и только новые знакомства:
        в старых чатах с историей (друзья, семья) скрипт сам не отвечает."""
        if not CFG.get("auto_adopt", True):
            return
        known = set(self.state["peers"].values())
        skip = self.state.setdefault("skip_ids", [])
        limit = CFG.get("auto_adopt_max_messages", 14)
        for c in self.page.evaluate(JS_UNREAD_ANY):
            pid = c["id"]
            if pid in known or pid in skip or pid == self.bot_peer or self.is_bot_name(c["title"]):
                continue
            if not self.running or not self.open_chat("id:" + pid):
                continue
            info = (self.page.evaluate(JS_CHAT_INFO) or "").strip()
            hist = self.page.evaluate(JS_HISTORY)
            why = None
            if not self.HUMAN_INFO.search(info):
                why = "не обычный пользователь (бот, канал или служебный чат)"
            elif len(hist) >= limit:
                why = f"старый чат с историей ({len(hist)} сообщений): похоже на знакомого, сам не отвечаю"
            if why:
                skip.append(pid)
                self.save()
                log("auto_skip", title=c["title"], why=why, hint="отвечать здесь: откройте чат в окне скрипта")
                continue
            h = self.page.evaluate("location.hash")
            key = h[2:] if h.startswith("#@") and not self.is_bot_name(h[2:]) else "id:" + pid
            if self.is_ignored(key):
                continue
            if key not in self.state["usernames"]:
                self.state["usernames"].append(key)
            self.state["peers"][key] = pid
            self.save()
            log("auto_adopt", chat=key, title=c["title"], msg="новый собеседник написал сам, отвечаю")
            self.answer(key)

    def log_unknown_unread(self):
        """Непрочитанное в чатах вне списка: не отвечаем (там могут быть друзья), но говорим об этом в консоли."""
        known = set(self.state["peers"].values())
        for c in self.page.evaluate(JS_UNREAD_ANY):
            if (c["id"] in known or c["id"] == self.bot_peer or c["title"].lower().rstrip().endswith("bot")
                    or c["id"] in self.state.get("skip_ids", [])):
                continue
            if time.time() - self.last_unknown_log.get(c["id"], 0) > 900:
                self.last_unknown_log[c["id"]] = time.time()
                log("unknown_unread", title=c["title"],
                    hint="чат вне списка автоответов: откройте его в окне скрипта или введите add @username")

    def start_new_chats(self):
        """Матчи, которым ещё не написали: пишем первыми."""
        for u in list(self.state["to_open"]):
            if not self.running or self.quit:
                return
            if self.is_bot_name(u):
                self.finish_open(u)
                continue
            if not self.open_chat(u):
                continue
            hist = self.chat_hist(u)
            if any(m["out"] for m in hist):
                self.finish_open(u)
                continue
            if hist and not hist[-1]["out"]:
                self.finish_open(u)  # она уже написала сама: дальше обычный ответ
                self.answer(u)
                continue
            opener_prompt, opener_shape, _tone, _level = realism.build_style(PERSONA, [], [])
            out = self.ask_model(opener_prompt, [{"role": "user", "content": OPENER_PROMPT}])
            if not out:
                log("opener", user=u, msg="модель не дала ответ, попробую позже")
                continue
            if out.get("action") == "escalate":
                self.state["paused"].append(u); self.finish_open(u)
                bell()
                log("ESCALATE", user=u, reason=out.get("reason"))
                continue
            texts = realism.enforce(out.get("messages", []), opener_shape)
            if texts and self.send_texts(u, texts):
                log("opener", user=u, sent=len(texts))
                self.finish_open(u)
                self.wait_for_reply(u)

    def finish_open(self, u):
        if u in self.state["to_open"]:
            self.state["to_open"].remove(u)
        if u not in self.state["opened"]:
            self.state["opened"].append(u)
        self.save()

    # ----- ПЕРЕПИСКА -----
    def refresh_peer_ids(self):
        """Запоминает peer-id чатов, чтобы потом дёшево смотреть значки непрочитанных в списке."""
        for u in self.all_usernames():
            if u in self.state["peers"]:
                continue
            if not self.open_chat(u):
                continue
            pid = None
            for _ in range(5):
                pid = self.page.evaluate(JS_ACTIVE_PEER)
                if pid:
                    break
                self.page.wait_for_timeout(400)
            if pid and (pid.startswith("-") or pid == self.bot_peer):
                log("peer", user=u, msg="это канал, группа или бот, убираю из списка чатов")
                if u in self.state["usernames"]:
                    self.state["usernames"].remove(u)
                if u in self.state["to_open"]:
                    self.state["to_open"].remove(u)
                self.state["paused"].append(u)
                self.save()
            elif pid:
                self.state["peers"][u] = pid
                self.save()
                self.answer(u)  # открытие чата пометило сообщения прочитанными: отвечаем сразу
            else:
                log("peer", user=u, msg="не удалось определить id чата")

    def unread_queue(self):
        peers = {self.state["peers"][u]: u for u in self.all_usernames()
                 if u in self.state["peers"] and u not in self.state["paused"]}
        if not peers:
            return []
        ids = self.page.evaluate(JS_UNREAD, list(peers.keys()))
        return [peers[i] for i in ids]

    def reply_loop(self):
        """Отвечаем всем, кто написал, по очереди; потом возвращаемся к лайкам.
        Раз в sweep_seconds обходим все чаты списка: страховка, если значок непрочитанного не сработал."""
        self.refresh_peer_ids()
        self.adopt_new_people()
        self.log_unknown_unread()
        sweep = time.time() - self.last_sweep > CFG.get("sweep_seconds", 12)
        done = set()
        while self.running and not self.quit:
            cand = self.unread_queue()
            if cand:
                log("unread", users=cand)
            if sweep:
                cand = list(dict.fromkeys(cand + [u for u in self.all_usernames() if u not in self.state["paused"]]))
                sweep = False
                self.last_sweep = time.time()
            cand = [u for u in cand if u not in done]
            if not cand:
                return
            for u in cand:
                if not self.running:
                    return
                if self.answer(u):
                    self.wait_for_reply(u)
                done.add(u)

    def chat_hist(self, u):
        """История открытого чата собеседника u без того, что было до команды remove (сброс: старую переписку бот «не видел»)."""
        hist = self.page.evaluate(JS_HISTORY)
        key = (u or "").lower().lstrip("@")
        if hist:
            mx = self.state.setdefault("maxmid", {})
            mx[key] = max(mx.get(key, 0), max(m["mid"] for m in hist))
        cut = self.state.get("forgot", {}).get(key)
        return [m for m in hist if m["mid"] > cut] if cut else hist

    def forget_chat(self, user):
        """Полный сброс собеседника: память, счётчики и вся прежняя переписка (по порогу id сообщений)."""
        key = user.lower().lstrip("@")
        st = self.state
        keys = {k for k in list(st.get("memory", {})) + list(st.get("last_replied", {})) + list(st.get("maxmid", {})) + list(st["peers"])
                if k.lower().lstrip("@") == key} | {user}
        cut = 0
        for k in keys:
            cut = max(cut, st.get("maxmid", {}).get(k.lower().lstrip("@"), 0))
        if cut:
            st.setdefault("forgot", {})[key] = cut
        for d in ("memory", "last_replied", "fails"):
            for k in list(st.get(d, {})):
                if k.lower().lstrip("@") == key:
                    st[d].pop(k)
        ra = getattr(self, "retry_after", {})
        for k in list(ra):
            if k.lower().lstrip("@") == key:
                ra.pop(k)
        return bool(cut)

    def _memory(self, key):
        mem = self.state.setdefault("memory", {}).setdefault(key, {"facts": [], "asked": []})
        mem.setdefault("facts", [])
        mem.setdefault("asked", [])
        return mem

    def memory_prompt_for(self, key, hist):
        """Что бот знает о собеседнице и о чём уже спрашивал (в том числе по тому, что видно в чате)."""
        mem = self._memory(key)
        asked = memory.merge_asked(mem["asked"], realism.recent_outgoing(hist, 40))
        return memory.memory_prompt(mem["facts"], asked)

    def remember(self, key, out, sent_texts):
        """После отправки: новые факты от модели и отправленные вопросы уходят в память чата."""
        mem = self._memory(key)
        mem["facts"] = memory.merge_facts(mem["facts"], (out or {}).get("facts"))
        mem["asked"] = memory.merge_asked(mem["asked"], sent_texts)
        self.save()

    def note(self, u, mid, why):
        """Причина, по которой не отвечаем: пишем в консоль один раз на сообщение, чтобы не спамить."""
        seen = self.__dict__.setdefault("_noted", set())
        if (u, mid, why) not in seen:
            seen.add((u, mid, why))
            log("skip", user=u, mid=mid, why=why)

    def wait_for_reply(self, u):
        """После нашего сообщения остаёмся в этом чате и ждём ответ chat_wait_seconds (30 с). Ответила: отвечаем и ждём снова.
        Молчит дольше, написала другая или бот остановлен: выходим (дальше другие чаты или лайки)."""
        wait = CFG.get("chat_wait_seconds", 30)
        if wait <= 0:
            return
        deadline = time.time() + wait
        while time.time() < deadline and not self.quit:
            if not self.nap(1.0):
                return
            if [x for x in self.unread_queue() if x != u]:
                return  # пишет кто-то ещё: идём отвечать ему
            hist = self.chat_hist(u)
            if hist and not hist[-1]["out"] and self.answer(u):
                deadline = time.time() + wait
        log("wait", user=u, msg=f"нет ответа {wait} с, возвращаюсь к лайкам")

    def answer(self, u):
        """True, если отправили ответ."""
        if self.is_bot_name(u) or self.is_ignored(u):
            return False
        if not self.open_chat(u):
            return False
        hist = self.chat_hist(u)
        if not hist or hist[-1]["out"]:
            return False  # последнее сообщение наше (или чат пуст): отвечать не на что
        last_mid = hist[-1]["mid"]
        msgs = []
        for m in hist[-40:]:
            text = strip_time(m["text"]) or ("[стикер/медиа]" if (m["sticker"] or m["photo"]) else "")
            if not text:
                continue
            role = "assistant" if m["out"] else "user"
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"] += "\n" + text
            else:
                msgs.append({"role": role, "content": text})
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        if not msgs or msgs[-1]["role"] != "user":
            self.note(u, last_mid, "в последнем сообщении нет текста (голосовое, кружок, файл?)")
            return False
        if time.time() < self.retry_after.get(u, 0):
            return False  # короткая пауза после отправки или сбоя
        fixed = canned.canned_reply(strip_time(hist[-1]["text"]))
        if fixed:  # обязательное правило: точная фраза получает точный ответ, модель не спрашиваем
            log("canned", user=u, trigger=strip_time(hist[-1]["text"]))
            if not self.send_texts(u, [fixed]):
                return False
            self.retry_after[u] = time.time() + 3
            return True
        her_texts = [strip_time(m["text"]) for m in hist if not m["out"] and m.get("text")][-4:]
        burst = 0
        for m_ in reversed(hist):
            if m_["out"]:
                break
            burst += 1
        style, shape, tone, level = realism.build_style(PERSONA, realism.recent_outgoing(hist, 5), her_texts, our_count=sum(1 for m in hist if m["out"]))
        old_shape = shape
        stale = realism.stale_topic([strip_time(m_["text"]) for m_ in hist], [m_["out"] for m_ in hist])
        if stale:
            style += realism.STALE_NOTE.format(topic=stale)
            log("topic", user=u, msg=f"тема застряла: {stale}, меняю тему")
        if burst >= 2 and not shape.allow_question:
            shape = next(s for s in realism.SHAPES if s.name == "statement_question")  # на новость о ней нужно уметь спросить
            style = style.replace(realism.shape_instruction(old_shape), realism.shape_instruction(shape))
        if burst >= 2:
            style += (f"\n\nОНА НАПИСАЛА {burst} СООБЩЕНИЯ ПОДРЯД (они склеены в последней реплике). Ответь на смысл ВСЕХ, не только на первое: "
                      "если в них есть новая информация о ней (работа, смена, планы), отреагируй и спроси подробность.")
        log("style", user=u, level=level, shape=shape.name, tone=tone.name, her_burst=burst)
        out = self.ask_model(style + "\n\n" + self.memory_prompt_for(u, hist), msgs)
        if not out:
            self.retry_after[u] = time.time() + 45
            log("reply", user=u, msg="модель не дала ответ, повторю через 45 с (чат не останавливаю)")
            return False
        self.state.get("fails", {}).pop(u, None)
        if out.get("action") == "escalate":
            self.state["paused"].append(u); self.save()
            bell()
            log("ESCALATE", user=u, reason=out.get("reason"), they_wrote=msgs[-1]["content"][-200:],
                hint=f"ответьте сами в чате; потом resumechat @{u}, и скрипт продолжит с новых сообщений")
            return False
        texts = realism.enforce(out.get("messages", []), shape)
        if not texts:
            self.note(u, last_mid, "модель вернула пустой ответ")
            return False
        if not self.send_texts(u, texts):
            return False
        self.remember(u, out, texts)
        self.retry_after[u] = time.time() + 3  # дать странице показать наше сообщение
        return True

    def send_texts(self, u, texts):
        exp = self.expected_peer(u)
        if exp and self.active_peer() != exp:
            log("BLOCKED", user=u, msg="открыт не тот чат, печать отменена")
            return False
        if not self.nap(random.uniform(*CFG["reply"]["read_delay"])):
            return False
        for t in texts:
            if self.dry:
                log("reply(dry)", user=u, text=t)
            else:
                if not self.type_human(t):
                    return False
                log("reply", user=u, text=t)
            self.nap(random.uniform(*CFG["reply"]["gap_between_messages"]))
        return True

    def ask_model(self, system, messages):
        """Ответ модели без банальных приветствий («привет», «как дела»): при таком ответе просим заново, в крайнем случае вырезаем."""
        extra = ""
        out = None
        for attempt in range(3):
            out = self._ask_model_once((system or "") + extra, messages)
            msgs = (out or {}).get("messages") or []
            if not has_greeting([m for m in msgs if isinstance(m, str)]):
                return out
            log("greeting", msg="в ответе было «привет/как дела», прошу переписать", attempt=attempt + 1)
            extra = "\n\nВ ПРОШЛОМ ВАРИАНТЕ БЫЛО ПРИВЕТСТВИЕ. Приветствия («привет», «как дела» и т. п.) ЗАПРЕЩЕНЫ: начни сразу с живой темы."
        out["messages"] = [m for m in out["messages"] if isinstance(m, str) and not has_greeting([m])]
        return out

    def _ask_model_once(self, system, messages):
        """Ответ модели как JSON из текстовых блоков. None, если разобрать нельзя (сырой ответ идёт в лог)."""
        r = self.client.messages.create(model=CFG["model"], max_tokens=2000,
                                        system=(system or "") + "\n\n" + SAFETY_RULES, messages=messages)
        raw = text_of(r)
        out = parse_json(raw)
        if out:
            return out
        parts = plain_reply(raw)
        if parts:
            log("model_plain", msg="модель ответила обычным текстом, использую его", n=len(parts))
            return {"action": "reply", "messages": parts}
        log("model_raw", stop=r.stop_reason, text=raw[:300])
        return None

    def in_bot_chat(self):
        """True, если сейчас открыт чат с ботом знакомств (или любой бот)."""
        h = self.page.evaluate("location.hash").lower()
        if h == ("#@" + CFG["bot_username"]).lower() or h.endswith("bot"):
            return True
        pid = self.page.evaluate(JS_ACTIVE_PEER)
        return bool(pid) and pid == self.bot_peer

    def is_bot_name(self, u):
        u = (u or "").lower().lstrip("@")
        return u == CFG["bot_username"].lower() or u.endswith("bot") or "leomatch" in u

    def type_human(self, text):
        """Печать в чате. В чате с ботом знакомств писать ЗАПРЕЩЕНО: там только кнопки (лайк, дизлайк)."""
        if self.in_bot_chat():
            log("BLOCKED", msg="попытка написать в чат бота знакомств отклонена", text=text[:40])
            return False
        lo, hi = CFG["reply"]["type_ms_per_char"]
        self.page.click(".input-message-input")
        self.page.keyboard.press("ControlOrMeta+a")  # ссылка t.me/...?text= подставляет в поле своё «Привет!»: стираем черновик
        self.page.keyboard.press("Backspace")
        for n, line in enumerate(text.split("\n")):
            if n:
                self.page.keyboard.press("Shift+Enter")  # перенос строки внутри одного сообщения
            for word in re.findall(r"\S+\s*", line):
                self.page.keyboard.type(word, delay=random.randint(lo, hi))
                if random.random() < 0.12:
                    self.page.wait_for_timeout(random.randint(250, 800))
        self.page.wait_for_timeout(random.randint(200, 600))
        self.page.keyboard.press("Enter")
        return True

    # ----- главный цикл -----
    def run(self):
        threading.Thread(target=self.console_thread, daemon=True).start()
        with sync_playwright() as pw:
            if not self.start_browser(pw):
                return
            if GUI:
                emit("ready", version="1.0")
                self.emit_state()
            else:
                say("Готово. Введите start, чтобы начать (help: список команд).")
                self.print_status()
            last_chat_poll = 0
            last_hb = 0
            last_emit = time.time()
            while not self.quit:
                self.process_cmds()
                if GUI and time.time() - last_emit > 5:
                    last_emit = time.time()
                    self.emit_state()
                if not self.running:
                    self.pause(0.3)
                    continue
                if self.in_manual_pause():
                    self.pause(0.2)  # владелец управляет страницей в панели браузера: бот не мешает
                    continue
                if time.time() - last_hb > 60:
                    last_hb = time.time()
                    log("alive", chats=self.all_usernames(), resolved=len(self.state["peers"]),
                        paused=self.state["paused"], likes=self.likes_today())
                try:
                    if self.chats_on:
                        self.adopt_current_chat()
                        self.open_matches()
                        self.start_new_chats()
                        if time.time() - last_chat_poll > 2:
                            self.reply_loop()
                            last_chat_poll = time.time()
                    if self.likes_on and self.running:
                        self.swipe_once()
                    else:
                        self.nap(2)
                except Exception as e:
                    log("error", err=repr(e)[:300])
                    self.nap(5)
            self.ctx.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="смотреть и решать, но не лайкать и не писать")
    ap.add_argument("--autostart", action="store_true", help="сразу начать после входа")
    ap.add_argument("--gui", action="store_true", help="режим приложения Russian Fishing 4: JSON-события на stdout")
    ap.add_argument("--demo", action="store_true", help="с --gui: события без браузера и API (для проверки приложения)")
    args = ap.parse_args()
    GUI = args.gui
    if args.demo:
        import demo_bot
        demo_bot.run()
        sys.exit(0)
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Нет ANTHROPIC_API_KEY. Положите его в файл rybalka/.env: ANTHROPIC_API_KEY=<ваш ключ>")
    bot = Rybalka(args.dry_run)
    if args.autostart:
        bot.running = True
    bot.run()
