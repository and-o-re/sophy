#!/usr/bin/env bash
# Inicia a Sophy a partir da pasta do projeto (funciona em qualquer caminho).
# Uso: ./scripts/ia-on.sh            (terminal + painel)
#      ./scripts/ia-on.sh --offline  (sem internet)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/main.py" "$@"
