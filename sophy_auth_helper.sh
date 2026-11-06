#!/usr/bin/env bash
set -e

SOPHY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="${1:-whatsapp}"

if [ "$MODE" = "whatsapp" ]; then
  echo "=================================================================="
  echo " 🌿 SOPHY — PAREAMENTO REAL DO WHATSAPP (whatsmeow Go Bridge)"
  echo "=================================================================="
  echo "1. Abra o WhatsApp no seu celular"
  echo "2. Toque em Configurações / Menu (⋮) -> Aparelhos Conectados -> Conectar um aparelho"
  echo "3. Escaneie o QR Code abaixo (ou na Aba 03 do Painel Desktop http://127.0.0.1:8080):"
  echo ""
  cd "$SOPHY_DIR/external_skills/whatsapp-mcp/whatsapp-bridge"
  export WHATSAPP_BRIDGE_PORT=3001
  exec ./whatsapp-bridge
elif [ "$MODE" = "google" ]; then
  echo "=================================================================="
  echo " 🌿 SOPHY — AUTENTICAÇÃO GOOGLE WORKSPACE (gws-cli OAuth2)"
  echo "=================================================================="
  if [ ! -f "$HOME/.config/gws-cli/client_secret.json" ]; then
    echo "⚠️  Arquivo ~/.config/gws-cli/client_secret.json ainda não encontrado!"
    echo "   Preencha GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET na Aba 03 do Painel (http://127.0.0.1:8080)"
    echo "   ou copie seu client_secret.json (Desktop App do Google Cloud Console) para:"
    echo "   ~/.config/gws-cli/client_secret.json"
    exit 1
  fi
  exec "$SOPHY_DIR/.venv/bin/uvx" gws-cli auth
else
  echo "Uso: $0 [whatsapp|google]"
fi
