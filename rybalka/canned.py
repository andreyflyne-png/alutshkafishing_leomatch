"""Обязательные точные ответы: если последнее сообщение собеседницы РОВНО такое (регистр и знаки на конце важны), бот отвечает заготовкой, без модели."""

_ERROR_503 = """ERROR [503]: Service Unavailable

{
  "error": {
    "type": "upstream_connection_failure",
    "message": "Anthropic API servers are not responding.",
    "code": "ANTHROPIC_SERVER_UNAVAILABLE",
    "status": 503,
    "details": {
      "service": "api.anthropic.com",
      "reason": "Connection timed out while waiting for upstream response.",
      "retryable": true
    }
  },
  "request": {
    "id": "req_8f3a2c91d7e64b10",
    "method": "POST",
    "endpoint": "/v1/messages"
  }
}

[CRITICAL] Request failed after 3 attempts.
Retry scheduled in 30 seconds..."""

CANNED = {"Скинь фото!": _ERROR_503}


def canned_reply(text):
    """Заготовка для точного совпадения (без strip регистра: «скинь фото!» или «Скинь фото» не подходят), иначе None."""
    return CANNED.get((text or "").strip())
