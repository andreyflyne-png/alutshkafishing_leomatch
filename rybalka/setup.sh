#!/bin/bash
# Однократная установка: виртуальное окружение, библиотеки, браузер для Playwright
cd "$(dirname "$0")"
export SSL_CERT_FILE=/etc/ssl/cert.pem PIP_CERT=/etc/ssl/cert.pem
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/playwright install chromium
[ -f .env ] || echo 'ANTHROPIC_API_KEY=вставьте_ключ_сюда' > .env
echo "Готово. Откройте rybalka/.env и вставьте ключ Anthropic API, потом запускайте ./run.sh"
