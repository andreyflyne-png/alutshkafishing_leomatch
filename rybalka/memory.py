"""Память о собеседнице: что бот уже знает и о чём уже спрашивал, чтобы не повторяться и не устраивать допрос."""
import re

from realism import is_question

MAX_FACTS = 30
MAX_ASKED = 12
MAX_FACT_LEN = 80
MAX_ASKED_LEN = 120


def _norm(text):
    return re.sub(r"\s+", " ", text.strip())


def merge_facts(old, new):
    """Добавляет новые короткие факты без повторов (регистр и пробелы не важны), хранит последние MAX_FACTS."""
    out = list(old)
    seen = {_norm(f).lower() for f in out}
    if isinstance(new, list):
        for fact in new:
            if not isinstance(fact, str):
                continue
            fact = _norm(fact)
            if not fact or len(fact) > MAX_FACT_LEN or fact.lower() in seen:
                continue
            out.append(fact)
            seen.add(fact.lower())
    return out[-MAX_FACTS:]


def merge_asked(old, sent_texts):
    """Запоминает вопросы, которые бот уже задавал (из отправленных или прочитанных сообщений)."""
    out = list(old)
    seen = {_norm(q).lower() for q in out}
    for text in sent_texts:
        if not isinstance(text, str) or not is_question(text):
            continue
        q = _norm(text)[:MAX_ASKED_LEN]
        if q.lower() not in seen:
            out.append(q)
            seen.add(q.lower())
    return out[-MAX_ASKED:]


def memory_prompt(facts, asked):
    lines = ["ПАМЯТЬ О СОБЕСЕДНИЦЕ (то, что ты уже знаешь из переписки):"]
    lines += [f"- {f}" for f in facts] if facts else ["- пока ничего"]
    if asked:
        lines.append("Ты уже спрашивал: " + "; ".join(f"«{q}»" for q in asked[-8:]) + ".")
    lines.append("Не спрашивай то, что уже известно из памяти или из переписки выше, и не повторяй прежние вопросы даже другими словами. "
                 "Не устраивай допрос: чаще реагируй на её слова, а о себе рассказывай сам. Если хочешь продолжить тему, опирайся на то, что она сказала.")
    lines.append('В поле "facts" ответа добавляй новые короткие факты о ней (имя, учёба, работа, район, увлечения, планы), '
                 "если они появились в её последних сообщениях; если нового нет, поле не добавляй.")
    return "\n".join(lines)
