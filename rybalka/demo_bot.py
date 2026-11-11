"""Демо-режим бота: те же события протокола, но без браузера, Telegram и API. Для тестов и скриншотов приложения."""
import base64
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from gui_io import emit, parse_command

CAP = 150
SCREEN_FILE = Path(__file__).parent / "data" / "screen.jpg"
# образец кадра зеркала (тёмная «страница» с полем поиска и строками чатов), чтобы проверять приложение без Telegram
SAMPLE_FRAME = base64.b64decode("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")


def run():
    cmds = queue.Queue()

    def reader():
        for line in sys.stdin:
            cmd = parse_command(line)
            if cmd:
                cmds.put(cmd)
        cmds.put({"cmd": "quit"})

    threading.Thread(target=reader, daemon=True).start()

    st = {"running": False, "likes_on": True, "chats_on": True, "auto_adopt": True, "dry_run": True, "likes_today": 12}
    chats = {"v_uviii": "active", "Anna": "active", "Kira": "paused"}

    def now():
        return datetime.now().strftime("%H:%M:%S")

    def say(kind, **fields):
        emit("log", t=now(), kind=kind, fields={k: str(v) for k, v in fields.items()})

    def push():
        emit("status", **st, cap=CAP)
        emit("chats", items=[{"name": n, "state": s, "peer": None} for n, s in chats.items()])

    emit("ready", version="demo")
    emit("chats", items=[{"name": n, "state": s, "peer": None} for n, s in chats.items()])
    emit("status", **st, cap=CAP)

    script = [
        ("profile", {"mid": 1, "like": True, "conf": 0.82, "reason": "Тёмные волосы, пирсинг, чокер, 19 лет"}),
        ("profile", {"mid": 2, "like": False, "conf": 0.9, "reason": "Обычный стиль, нет альт-признаков"}),
        ("reply", {"user": "Anna", "text": "ну привет, смелая ты)"}),
        ("profile", {"mid": 3, "like": False, "conf": 1, "reason": "возраст не 18+ или не найден"}),
        ("match", {"name": "えっ?", "msg": "взаимная симпатия, открою чат и напишу первым"}),
        ("opener", {"user": "えっ?", "sent": 2}),
    ]
    i, started_at, alerted, last_tick = 0, None, False, 0.0
    mirror, frame_n, last_frame = None, 0, 0.0
    while True:
        try:
            cmd = cmds.get(timeout=0.2)
        except queue.Empty:
            cmd = None
        if cmd:
            name, user = cmd.get("cmd"), str(cmd.get("user", "")).lstrip("@")
            if name == "start":
                st["running"], started_at = True, time.time()
            elif name == "stop":
                st["running"] = False
            elif name == "set":
                key = {"likes": "likes_on", "chats": "chats_on", "auto": "auto_adopt"}.get(cmd.get("key"))
                if key:
                    st[key] = bool(cmd.get("value"))
            elif name == "add" and user:
                chats[user] = "active"
            elif name == "remove" and user:
                chats[user] = "ignored"
            elif name == "resume" and user and chats.get(user) == "paused":
                chats[user] = "active"
            elif name == "reload_config":
                say("console", text="настройки перечитаны")
            elif name == "mirror":
                mirror = (int(cmd.get("w") or 700), int(cmd.get("h") or 600)) if cmd.get("on") else None
            elif name == "input":
                say("mirror_input", input=cmd.get("kind"), x=cmd.get("x"), y=cmd.get("y"), text=cmd.get("text"))
                continue
            elif name == "quit":
                return
            push()
        if mirror and time.time() - last_frame >= 0.5:
            last_frame = time.time()
            SCREEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = SCREEN_FILE.with_suffix(".tmp")
            tmp.write_bytes(SAMPLE_FRAME)
            os.replace(tmp, SCREEN_FILE)
            frame_n += 1
            emit("frame", n=frame_n, w=mirror[0], h=mirror[1])
        if st["running"] and time.time() - last_tick >= 2:
            last_tick = time.time()
            kind, fields = script[i % len(script)]
            i += 1
            if kind == "profile" and fields["like"]:
                st["likes_today"] += 1
            say(kind, **fields)
            emit("status", **st, cap=CAP)
        if st["running"] and not alerted and started_at and time.time() - started_at > 10:
            alerted = True
            chats["v_uviii"] = "paused"
            say("ESCALATE", user="v_uviii", reason="Собеседница прямо спросила, бот ли это", they_wrote="ты вообще бот?")
            emit("alert", user="v_uviii", reason="Собеседница прямо спросила, бот ли это", they_wrote="ты вообще бот?")
            push()
