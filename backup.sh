#!/usr/bin/env bash
# Backup de la biblioteca/historial/configuración de ytaudio (~/.config/ytaudio)
set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/ytaudio"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$SRC_DIR/backups/$(date +%Y%m%d-%H%M%S)"

if [ ! -d "$CONFIG_DIR" ]; then
  echo "No se encontró $CONFIG_DIR — todavía no hay nada que respaldar." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

copied=0
for f in library.json history.json settings.json; do
  if [ -f "$CONFIG_DIR/$f" ]; then
    cp "$CONFIG_DIR/$f" "$BACKUP_DIR/$f"
    echo "Copiado: $f"
    copied=$((copied + 1))
  fi
done

if [ "$copied" -eq 0 ]; then
  rmdir "$BACKUP_DIR"
  echo "No había archivos para respaldar." >&2
  exit 1
fi

echo "Backup guardado en: $BACKUP_DIR"
