#!/usr/bin/env bash
# Instala ytaudio como comando global en ~/.local/bin
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/.local/bin"
mkdir -p "$DEST_DIR"

if [ ! -x "$SRC_DIR/.venv/bin/python3" ]; then
  echo "No se encontró $SRC_DIR/.venv — creá el venv primero:" >&2
  echo "  python3 -m venv \"$SRC_DIR/.venv\" && \"$SRC_DIR/.venv/bin/pip\" install textual dbus-next yt-dlp" >&2
  exit 1
fi

rm -f "$DEST_DIR/ytaudio"
cat > "$DEST_DIR/ytaudio" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="$SRC_DIR" "$SRC_DIR/.venv/bin/python3" -m ytaudio "\$@"
EOF
chmod +x "$DEST_DIR/ytaudio"

echo "Instalado en $DEST_DIR/ytaudio"
case ":$PATH:" in
  *":$DEST_DIR:"*) ;;
  *) echo "Añade esto a tu ~/.zshrc o ~/.bashrc: export PATH=\"$DEST_DIR:\$PATH\"" ;;
esac
