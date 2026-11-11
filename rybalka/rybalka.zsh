# Команда `rybalka run` (доп. флаги: rybalka run --dry-run). Подключите: source /путь/к/rybalka/rybalka.zsh
RYBALKA_DIR="${0:A:h}"
rybalka() {
  if [ "$1" = "run" ]; then
    shift
    (cd "$RYBALKA_DIR" && ./run.sh "$@")
  else
    echo "использование: rybalka run [--dry-run] [--autostart]"
  fi
}
