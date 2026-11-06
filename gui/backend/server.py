"""
Backend FastAPI Local (127.0.0.1:8080) - FASE 4.
Serve a GUI Webview desacoplada 100% offline e expõe APIs para:
- Monitoramento em tempo real de VRAM da RTX 4050 (`nvidia-smi`), RAM, SSD e modelos carregados no Ollama (`/api/ps`).
- Botão de Emergência "Modo Estritamente Offline" (`STRICT_OFFLINE_MODE`).
- Seletor e validador de diretórios (`OBSIDIAN_VAULT_PATH` e `ALLOWED_CODE_DIRS`).
- Gerenciador de credenciais visuais (Discord Token, Google OAuth2 Client JSON, QR Code / Pareamento WhatsApp).
- Editor visual da Personalidade (`/00_Persona/personalidade_principal.md`) e Chat Rápido com o Agente.
"""

import asyncio
from pathlib import Path
import platform
import re
import subprocess
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import ollama
import psutil
from pydantic import BaseModel

from config import SOPHY_VERSION, Settings, settings
from core.agent_core import LocalHybridAgent
from database.db_manager import db_manager
from tools.google_workspace_tools import ensure_gws_client_secret_file, get_gws_auth_status
from tools.hardware_tools import (
    activate_avell_hardware_driver,
    auto_adjust_hardware_by_context,
    control_keyboard_rgb,
    control_notebook_fans,
    get_fans_and_rgb_status,
)
from tools.rag_tools import index_obsidian_to_rag
from tools.whatsapp_tools import ensure_whatsapp_bridge_running
from workers.discord_bot import get_discord_live_status, sophy_discord_service
from workers.sync_manager import sync_manager

app = FastAPI(
    title="IA Local Híbrida — Painel de Configurações Desktop",
    version=SOPHY_VERSION,
)


@app.on_event("startup")
async def _startup_background_services() -> None:
    """Inicia o cliente Discord Gateway / Ponte em tempo real e o loop de sincronização."""
    sophy_discord_service.start_background()
    sync_manager.start_background_loop(interval_seconds=300)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ENV_FILE_PATH = Path(__file__).resolve().parent.parent.parent / ".env"

# Instância compartilhada do Agente para testes rápidos pelo Painel
_gui_agent: Optional[LocalHybridAgent] = None


def get_gui_agent() -> LocalHybridAgent:
    global _gui_agent
    if _gui_agent is None:
        _gui_agent = LocalHybridAgent()
    return _gui_agent


# =====================================================================
# HELPERS DE PERSISTÊNCIA NO .ENV E MONITOR DE HARDWARE (RTX 4050)
# =====================================================================

def update_env_variables(updates: Dict[str, str]) -> None:
    """Atualiza ou adiciona chaves no arquivo `.env` e recarrega `settings` em memória."""
    if not ENV_FILE_PATH.exists():
        ENV_FILE_PATH.write_text("", encoding="utf-8")

    lines = ENV_FILE_PATH.read_text(encoding="utf-8").splitlines()
    updated_keys = set()
    new_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key, _ = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    ENV_FILE_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # Recarrega atributos no singleton `settings`
    refreshed = Settings()
    for field_name in refreshed.model_fields:
        setattr(settings, field_name, getattr(refreshed, field_name))
    settings.ensure_directories()


def read_env_dict() -> Dict[str, str]:
    """Lê todas as chaves/valores atuais do arquivo `.env`."""
    data: Dict[str, str] = {}
    if not ENV_FILE_PATH.exists():
        return data
    for line in ENV_FILE_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def query_rtx4050_vram() -> Dict[str, Any]:
    """
    Consulta a GPU NVIDIA RTX 4050 via `nvidia-smi` sem depender de libs pesadas,
    retornando VRAM usada, total, temperatura, potência e processos na GPU.
    """
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=name,memory.used,memory.total,memory.free,utilization.gpu,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, text=True, timeout=2.0).strip()
        parts = [p.strip() for p in out.split(",")]
        used_mb = float(parts[1])
        total_mb = float(parts[2])
        free_mb = float(parts[3])
        util_pct = float(parts[4])
        temp_c = float(parts[5])
        power_w = float(parts[6]) if parts[6] != "[N/A]" else 0.0

        # Verifica quanto o processo do Ollama está usando de VRAM
        proc_cmd = [
            "nvidia-smi",
            "--query-compute-apps=process_name,used_memory",
            "--format=csv,noheader,nounits",
        ]
        proc_out = subprocess.check_output(proc_cmd, text=True, timeout=2.0).strip()
        ollama_vram_mb = 0.0
        for line in proc_out.splitlines():
            if "ollama" in line.lower() or "llama" in line.lower():
                p_parts = [x.strip() for x in line.split(",")]
                if len(p_parts) == 2 and p_parts[1].isdigit():
                    ollama_vram_mb += float(p_parts[1])

        return {
            "available": True,
            "gpu_name": parts[0],
            "vram_used_mb": round(used_mb, 1),
            "vram_total_mb": round(total_mb, 1),
            "vram_free_mb": round(free_mb, 1),
            "vram_percent": round((used_mb / total_mb) * 100.0, 1) if total_mb else 0.0,
            "ollama_vram_mb": round(ollama_vram_mb, 1),
            "gpu_utilization_pct": util_pct,
            "temperature_c": temp_c,
            "power_draw_w": power_w,
        }
    except Exception as exc:
        return {
            "available": False,
            "gpu_name": "NVIDIA RTX 4050 (Indisponível)",
            "vram_used_mb": 0.0,
            "vram_total_mb": 6144.0,
            "vram_free_mb": 6144.0,
            "vram_percent": 0.0,
            "ollama_vram_mb": 0.0,
            "gpu_utilization_pct": 0.0,
            "temperature_c": 0.0,
            "power_draw_w": 0.0,
            "error": str(exc),
        }


# =====================================================================
# MODELOS DE PAYLOAD (PYDANTIC)
# =====================================================================

class OfflineTogglePayload(BaseModel):
    strict_offline_mode: bool


class DirectoriesPayload(BaseModel):
    obsidian_vault_path: str
    allowed_code_dirs: str


class CredentialsPayload(BaseModel):
    openai_code_api_key: Optional[str] = None
    google_ai_studio_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    discord_bot_token: Optional[str] = None
    discord_guild_id: Optional[str] = None
    discord_channel_id: Optional[str] = None
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    whatsapp_bridge_port: Optional[str] = None
    composio_api_key: Optional[str] = None
    finance_fintel_api_key: Optional[str] = None
    finance_adanos_api_key: Optional[str] = None
    alpha_vantage_api_key: Optional[str] = None
    spotipy_client_id: Optional[str] = None
    spotipy_client_secret: Optional[str] = None


class PersonaUpdatePayload(BaseModel):
    content: str


class AgentQuickChatPayload(BaseModel):
    prompt: str


class SyncRunPayload(BaseModel):
    target: str = "all"


class FanControlPayload(BaseModel):
    mode: str = "balanced"
    speed_percent: int = 60


class RgbControlPayload(BaseModel):
    color_or_preset: str = "ruinas_verde"
    brightness_percent: int = 80
    effect: str = "static"


# =====================================================================
# ENDPOINTS REST DA GUI DESACOPLADA
# =====================================================================

@app.get("/api/telemetry")
async def get_telemetry() -> Dict[str, Any]:
    """Retorna métricas em tempo real de VRAM (RTX 4050), RAM, SSD, Ollama e contagens SQLite."""
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(settings.OBSIDIAN_VAULT_PATH.parent))
    gpu_info = query_rtx4050_vram()

    ollama_models_loaded = []
    ollama_online = False
    try:
        client = ollama.AsyncClient(host=settings.OLLAMA_BASE_URL)
        ps_resp = await client.ps()
        ollama_online = True
        for m in ps_resp.get("models", []):
            size_vram = m.get("size_vram", 0)
            size_total = m.get("size", 1)
            gpu_pct = round((size_vram / size_total) * 100.0, 1) if size_total else 0.0
            ollama_models_loaded.append(
                {
                    "name": m.get("name"),
                    "size_mb": round(size_total / (1024**2), 1),
                    "vram_mb": round(size_vram / (1024**2), 1),
                    "gpu_offload_pct": gpu_pct,
                }
            )
    except Exception:
        ollama_online = False

    import json as _json
    db_summary = {t["table"]: t["row_count"] for t in db_manager.get_schema_summary()}
    hw_controls = _json.loads(get_fans_and_rgb_status())

    return {
        "strict_offline_mode": settings.STRICT_OFFLINE_MODE,
        "gpu": gpu_info,
        "hardware_controls": hw_controls,
        "ram": {
            "used_gb": round(vm.used / (1024**3), 2),
            "total_gb": round(vm.total / (1024**3), 2),
            "percent": vm.percent,
        },
        "disk": {
            "free_gb": round(disk.free / (1024**3), 2),
            "total_gb": round(disk.total / (1024**3), 2),
            "percent": disk.percent,
        },
        "ollama": {
            "online": ollama_online,
            "base_url": settings.OLLAMA_BASE_URL,
            "reasoning_model": settings.OLLAMA_REASONING_MODEL,
            "embed_model": settings.OLLAMA_EMBED_MODEL,
            "num_ctx": settings.OLLAMA_NUM_CTX,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "loaded_models": ollama_models_loaded,
        },
        "cloud_ai": {
            "prefer_cloud_when_online": settings.PREFER_CLOUD_WHEN_ONLINE,
            "openai_code_configured": bool(settings.OPENAI_CODE_API_KEY.strip()),
            "google_ai_studio_configured": bool(settings.GOOGLE_AI_STUDIO_API_KEY.strip()),
        },
        "database_counts": db_summary,
    }


@app.get("/api/config")
async def get_current_config() -> Dict[str, Any]:
    """Retorna caminhos configurados, estado das credenciais (mascaradas) e persona atual."""
    env_data = read_env_dict()

    def _mask(val: str) -> str:
        if not val or len(val) < 8:
            return ""
        return f"{val[:4]}{'*' * (len(val) - 8)}{val[-4:]}"

    persona_text = ""
    if settings.persona_file_path.exists():
        persona_text = settings.persona_file_path.read_text(encoding="utf-8")

    return {
        "strict_offline_mode": settings.STRICT_OFFLINE_MODE,
        "obsidian_vault_path": str(settings.OBSIDIAN_VAULT_PATH),
        "allowed_code_dirs": settings.ALLOWED_CODE_DIRS,
        "timezone": settings.TIMEZONE,
        "max_tool_iterations": settings.MAX_TOOL_ITERATIONS,
        "credentials_status": {
            "openai_code_configured": bool(env_data.get("OPENAI_CODE_API_KEY")),
            "openai_code_masked": _mask(env_data.get("OPENAI_CODE_API_KEY", "")),
            "google_ai_studio_configured": bool(env_data.get("GOOGLE_AI_STUDIO_API_KEY")),
            "google_ai_studio_masked": _mask(env_data.get("GOOGLE_AI_STUDIO_API_KEY", "")),
            "anthropic_configured": bool(env_data.get("ANTHROPIC_API_KEY")),
            "anthropic_masked": _mask(env_data.get("ANTHROPIC_API_KEY", "")),
            "discord_configured": bool(env_data.get("DISCORD_BOT_TOKEN")),
            "discord_token_masked": _mask(env_data.get("DISCORD_BOT_TOKEN", "")),
            "discord_guild_id": env_data.get("DISCORD_GUILD_ID", settings.DISCORD_GUILD_ID),
            "discord_channel_id": env_data.get("DISCORD_CHANNEL_ID", settings.DISCORD_CHANNEL_ID),
            "discord_live_status": await get_discord_live_status(),
            "google_configured": bool(env_data.get("GOOGLE_CLIENT_ID") and env_data.get("GOOGLE_CLIENT_SECRET")),
            "google_client_id": env_data.get("GOOGLE_CLIENT_ID", ""),
            "google_workspace_status": get_gws_auth_status(),
            "whatsapp_paired": env_data.get("WHATSAPP_PAIRED", "false").lower() == "true",
            "whatsapp_bridge_port": env_data.get("WHATSAPP_BRIDGE_PORT", "3001"),
            "composio_configured": bool(env_data.get("COMPOSIO_API_KEY")),
            "composio_masked": _mask(env_data.get("COMPOSIO_API_KEY", "")),
            "finance_fintel_configured": bool(env_data.get("FINANCE_FINTEL_API_KEY")),
            "finance_adanos_configured": bool(env_data.get("FINANCE_ADANOS_API_KEY")),
            "alpha_vantage_configured": bool(env_data.get("ALPHA_VANTAGE_API_KEY")),
            "spotipy_configured": bool(env_data.get("SPOTIPY_CLIENT_ID") and env_data.get("SPOTIPY_CLIENT_SECRET")),
            "spotipy_client_id": env_data.get("SPOTIPY_CLIENT_ID", ""),
        },
        "persona_markdown": persona_text,
    }


@app.post("/api/offline-mode")
async def toggle_offline_mode(payload: OfflineTogglePayload) -> Dict[str, Any]:
    """Ativa ou desativa imediatamente o Botão de Emergência 'Modo Estritamente Offline'."""
    val_str = "true" if payload.strict_offline_mode else "false"
    update_env_variables({"STRICT_OFFLINE_MODE": val_str})
    return {
        "status": "OK",
        "strict_offline_mode": settings.STRICT_OFFLINE_MODE,
        "message": (
            "MODO ESTRITAMENTE OFFLINE ATIVADO: Workers externos bloqueados."
            if settings.STRICT_OFFLINE_MODE
            else "Modo Híbrido restaurado: Workers autorizados a sincronizar."
        ),
    }


@app.post("/api/directories")
async def save_directories(payload: DirectoriesPayload) -> Dict[str, Any]:
    """Salva e valida os diretórios do Vault do Obsidian e de Código Permitido."""
    vault_path = Path(payload.obsidian_vault_path).expanduser().resolve()
    vault_path.mkdir(parents=True, exist_ok=True)

    cleaned_code_dirs = []
    for raw_dir in payload.allowed_code_dirs.split(","):
        if raw_dir.strip():
            p = Path(raw_dir.strip()).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            cleaned_code_dirs.append(str(p))

    if not cleaned_code_dirs:
        raise HTTPException(status_code=400, detail="Informe ao menos um diretório em ALLOWED_CODE_DIRS.")

    update_env_variables(
        {
            "OBSIDIAN_VAULT_PATH": str(vault_path),
            "ALLOWED_CODE_DIRS": ",".join(cleaned_code_dirs),
        }
    )
    return {
        "status": "SAVED",
        "obsidian_vault_path": str(settings.OBSIDIAN_VAULT_PATH),
        "allowed_code_dirs": settings.ALLOWED_CODE_DIRS,
    }


@app.get("/api/browse-folders")
async def browse_local_folders(base_path: str = "~") -> Dict[str, Any]:
    """Lista subpastas locais para o seletor visual de diretórios da GUI."""
    target = Path(base_path).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        target = Path.home().resolve()

    folders = []
    try:
        for entry in sorted(target.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                folders.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        pass

    return {
        "current_path": str(target),
        "parent_path": str(target.parent) if target.parent != target else str(target),
        "folders": folders,
    }


@app.post("/api/credentials")
async def save_credentials(payload: CredentialsPayload) -> Dict[str, Any]:
    """Salva credenciais de IA Híbrida e sincronização (OpenAI Code, Google AI Studio, Claude, Discord, Google OAuth2/Workspace, WhatsApp, Finanças, Composio, Spotify) no `.env` local."""
    updates: Dict[str, str] = {}
    if payload.openai_code_api_key is not None and payload.openai_code_api_key.strip():
        updates["OPENAI_CODE_API_KEY"] = payload.openai_code_api_key.strip()
    if payload.google_ai_studio_api_key is not None and payload.google_ai_studio_api_key.strip():
        updates["GOOGLE_AI_STUDIO_API_KEY"] = payload.google_ai_studio_api_key.strip()
    if payload.anthropic_api_key is not None and payload.anthropic_api_key.strip():
        updates["ANTHROPIC_API_KEY"] = payload.anthropic_api_key.strip()
    if payload.discord_bot_token is not None and payload.discord_bot_token.strip():
        updates["DISCORD_BOT_TOKEN"] = payload.discord_bot_token.strip()
    if payload.discord_guild_id is not None:
        updates["DISCORD_GUILD_ID"] = payload.discord_guild_id.strip()
    if payload.discord_channel_id is not None:
        updates["DISCORD_CHANNEL_ID"] = payload.discord_channel_id.strip()
    if payload.google_client_id is not None and payload.google_client_id.strip():
        updates["GOOGLE_CLIENT_ID"] = payload.google_client_id.strip()
    if payload.google_client_secret is not None and payload.google_client_secret.strip():
        updates["GOOGLE_CLIENT_SECRET"] = payload.google_client_secret.strip()
    if payload.whatsapp_bridge_port is not None and payload.whatsapp_bridge_port.strip():
        updates["WHATSAPP_BRIDGE_PORT"] = payload.whatsapp_bridge_port.strip()
    if payload.composio_api_key is not None and payload.composio_api_key.strip():
        updates["COMPOSIO_API_KEY"] = payload.composio_api_key.strip()
    if payload.finance_fintel_api_key is not None and payload.finance_fintel_api_key.strip():
        updates["FINANCE_FINTEL_API_KEY"] = payload.finance_fintel_api_key.strip()
    if payload.finance_adanos_api_key is not None and payload.finance_adanos_api_key.strip():
        updates["FINANCE_ADANOS_API_KEY"] = payload.finance_adanos_api_key.strip()
    if payload.alpha_vantage_api_key is not None and payload.alpha_vantage_api_key.strip():
        updates["ALPHA_VANTAGE_API_KEY"] = payload.alpha_vantage_api_key.strip()
    if payload.spotipy_client_id is not None and payload.spotipy_client_id.strip():
        updates["SPOTIPY_CLIENT_ID"] = payload.spotipy_client_id.strip()
    if payload.spotipy_client_secret is not None and payload.spotipy_client_secret.strip():
        updates["SPOTIPY_CLIENT_SECRET"] = payload.spotipy_client_secret.strip()

    if updates:
        update_env_variables(updates)
        # Se salvou Google Client ID + Secret, gera automaticamente ~/.config/gws-cli/client_secret.json
        ensure_gws_client_secret_file()

    return {"status": "CREDENTIALS_SAVED", "updated_fields": list(updates.keys())}


@app.get("/api/whatsapp/qr")
async def get_whatsapp_qr_session() -> Dict[str, Any]:
    """
    Inicia/consulta o `whatsapp-bridge` real (`whatsmeow` na porta 3001) e retorna o QR Code real
    (inclusive em formato Base64 PNG escaneável pelo celular) ou o status de conexão ativa!
    """
    if settings.STRICT_OFFLINE_MODE:
        raise HTTPException(
            status_code=403,
            detail="Modo Estritamente Offline está ativo. Desative-o para gerar sessão de QR Code do WhatsApp.",
        )

    import base64
    import io

    state = await asyncio.to_thread(ensure_whatsapp_bridge_running)
    qr_raw = state.get("qr_code") or state.get("qr_payload") or ""
    paired = bool(state.get("paired"))
    connected = bool(state.get("connected"))

    if paired:
        update_env_variables({"WHATSAPP_PAIRED": "true"})

    qr_png_b64 = ""
    if qr_raw:
        try:
            import qrcode
            import qrcode.image.svg

            qr = qrcode.QRCode(box_size=8, border=3)
            qr.add_data(qr_raw)
            qr.make(fit=True)
            svg_img = qr.make_image(
                image_factory=qrcode.image.svg.SvgPathFillImage,
            )
            buf = io.BytesIO()
            svg_img.save(buf)
            qr_png_b64 = "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as exc:
            qr_png_b64 = ""

    return {
        "status": "PAIRED_ONLINE" if (paired and connected) else ("PAIRED" if paired else "AWAITING_SCAN"),
        "paired": paired,
        "connected": connected,
        "jid": state.get("jid", ""),
        "qr_payload": qr_raw or "SOPHY_WPP_BRIDGE_READY",
        "qr_image_data_url": qr_png_b64,
        "qr_ascii": state.get("qr_ascii", ""),
        "bridge_port": state.get("bridge_port", settings.WHATSAPP_BRIDGE_PORT),
    }


@app.post("/api/google/auth-start")
async def start_google_workspace_oauth() -> Dict[str, Any]:
    """
    Gera `~/.config/gws-cli/client_secret.json` (se `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`
    estiverem no `.env`) e dispara o fluxo `uvx gws-cli auth` que abre o navegador para login Google.
    """
    if settings.STRICT_OFFLINE_MODE:
        raise HTTPException(status_code=403, detail="Modo Estritamente Offline está ativo.")

    status = get_gws_auth_status()
    if not status["client_secret_exists"]:
        return {
            "status": "MISSING_CLIENT_SECRET",
            "message": (
                "Preencha primeiro o GOOGLE WORKSPACE OAUTH2 CLIENT ID e CLIENT SECRET na coluna à esquerda "
                "e clique em [GUARDAR CHAVES NO COFRE (.ENV)] (ou salve o arquivo em ~/.config/gws-cli/client_secret.json)."
            ),
        }

    subprocess.Popen(
        [status["uvx_binary"], "gws-cli", "auth"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "status": "BROWSER_AUTH_STARTED",
        "message": "Fluxo OAuth2 iniciado! Conclua a autorização da sua conta Google na aba que abriu no navegador.",
    }


@app.post("/api/persona")
async def save_persona_markdown(payload: PersonaUpdatePayload) -> Dict[str, Any]:
    """Atualiza diretamente `/00_Persona/personalidade_principal.md` no Vault do Obsidian."""
    settings.ensure_directories()
    settings.persona_file_path.write_text(payload.content.strip() + "\n", encoding="utf-8")
    return {
        "status": "PERSONA_UPDATED",
        "path": str(settings.persona_file_path),
    }


@app.post("/api/rag/reindex")
async def trigger_rag_reindex() -> Dict[str, Any]:
    """Dispara a re-indexação vetorial do Vault no ChromaDB via `nomic-embed-text`."""
    import json as _json
    raw = index_obsidian_to_rag()
    return _json.loads(raw)


@app.post("/api/workers/sync")
async def run_workers_sync(payload: SyncRunPayload) -> Dict[str, Any]:
    """Dispara os workers de sincronização (Discord, Gmail, Drive, WhatsApp) sob demanda."""
    return await sync_manager.sync_selected(payload.target)


@app.get("/api/workers/logs")
async def get_workers_logs() -> Dict[str, Any]:
    """Retorna os últimos logs de sincronização registrados no SQLite."""
    return {
        "strict_offline_mode": settings.STRICT_OFFLINE_MODE,
        "logs": sync_manager.get_recent_sync_logs(limit=12),
    }


@app.get("/api/hardware/controls")
async def api_get_hardware_controls() -> Dict[str, Any]:
    """Retorna o estado atual das fans, temperaturas e RGB do teclado (seu notebook)."""
    import json as _json
    return _json.loads(get_fans_and_rgb_status())


@app.post("/api/hardware/fans")
async def api_set_fans(payload: FanControlPayload) -> Dict[str, Any]:
    """Altera o modo ou velocidade das fans do notebook (pode levar ~14 s; roda fora do event loop)."""
    import json as _json
    return _json.loads(
        await asyncio.to_thread(control_notebook_fans, mode=payload.mode, speed_percent=payload.speed_percent)
    )


@app.post("/api/hardware/rgb")
async def api_set_keyboard_rgb(payload: RgbControlPayload) -> Dict[str, Any]:
    """Altera a cor, preset, brilho ou efeito do RGB do teclado."""
    import json as _json
    return _json.loads(
        control_keyboard_rgb(
            color_or_preset=payload.color_or_preset,
            brightness_percent=payload.brightness_percent,
            effect=payload.effect,
        )
    )


@app.post("/api/hardware/auto")
async def api_auto_hardware() -> Dict[str, Any]:
    """Aciona o auto-ajuste inteligente da Sophya para Fans e RGB com base em temperatura e horário."""
    import json as _json
    return _json.loads(await asyncio.to_thread(auto_adjust_hardware_by_context, reason="painel_desktop"))


@app.post("/api/hardware/activate-driver")
async def api_activate_driver() -> Dict[str, Any]:
    """Carrega os módulos do `tuxedo-drivers` (fans + RGB) via pkexec, se ainda não estiverem ativos."""
    return await asyncio.to_thread(activate_avell_hardware_driver)


@app.post("/api/agent/chat")
async def quick_agent_chat(payload: AgentQuickChatPayload) -> Dict[str, Any]:
    """Envia um prompt direto do Painel Desktop para o Agente Local e retorna as tools acionadas."""
    agent = get_gui_agent()
    tools_called: List[Dict[str, Any]] = []

    def _capture_tool(name: str, args: Dict[str, Any], res: Dict[str, Any], it: int) -> None:
        tools_called.append(
            {
                "tool": name,
                "args": args,
                "ok": res.get("ok", False),
                "iteration": it,
            }
        )

    reply = await agent.chat(payload.prompt, on_tool_event=_capture_tool)
    return {
        "status": "OK",
        "reply": reply,
        "provider": agent.last_provider_used,
        "tools_called": tools_called,
    }


# =====================================================================
# ARQUIVOS ESTÁTICOS DO FRONTEND OFFLINE
# =====================================================================

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))
