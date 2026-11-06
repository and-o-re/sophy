#!/usr/bin/env bash
# Cria o comando `sophy` em ~/.local/bin, para iniciar a Sophy digitando o nome no terminal.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/sophy" <<LAUNCHER
#!/usr/bin/env bash
# Atalho da Sophy (gerado por instalar_atalho.sh)
if [[ "\${1:-}" == "--offline" ]]; then
    export STRICT_OFFLINE_MODE=true
    shift
fi
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/main.py" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/sophy"
echo "Atalho criado: $BIN_DIR/sophy"
echo "Se 'sophy' não for encontrado, adicione $BIN_DIR ao PATH."
