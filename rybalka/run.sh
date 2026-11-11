#!/bin/bash
# Запуск. Аргументы: --dry-run (не лайкать и не писать), --autostart (сразу начать)
cd "$(dirname "$0")"
exec .venv/bin/python bot.py "$@"
