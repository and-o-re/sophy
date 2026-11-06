"""
Workers Assíncronos de Sincronização Online (FASE 5 - Arquitetura Offline-First).
Sincroniza em segundo plano (ou sob demanda):
- Discord (`DiscordSyncWorker` -> `messages_cache`)
- Gmail (`GmailSyncWorker` -> `emails_cache`)
- Google Drive (`GoogleDriveSyncWorker` -> `OBSIDIAN_VAULT_PATH/04_Drive_Sync/` + `calendar_events`)
- WhatsApp Bridge (`WhatsAppSyncWorker` -> `messages_cache`)

Regras Estritas de Engenharia:
1. Antes de qualquer requisição de rede, verifica `settings.STRICT_OFFLINE_MODE`.
   Se estiver `True` (Kill-Switch ativo), aborta imediatamente com zero tráfego externo e grava
   `OFFLINE_SKIPPED` em `sync_status_log`.
2. Persiste tudo localmente no SQLite (`hybrid_ai.db`) e no Vault do Obsidian para consulta
   instantânea 100% offline pelo Agente via `run_sql_report`.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from config import settings
from database.db_manager import db_manager, get_now_iso


def _read_env_credentials() -> Dict[str, str]:
    """Lê credenciais diretamente do arquivo `.env` atualizado pela GUI."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    creds: Dict[str, str] = {}
    if not env_file.exists():
        return creds
    for line in env_file.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            creds[k.strip()] = v.strip()
    return creds


def _log_sync_event(worker_name: str, status: str, items_synced: int, details: str) -> Dict[str, Any]:
    """Registra a execução do worker na tabela de auditoria `sync_status_log` do SQLite."""
    now_str = get_now_iso()
    with db_manager.get_connection(read_only=False) as conn:
        conn.execute(
            """
            INSERT INTO sync_status_log (worker_name, status, items_synced, details, executed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (worker_name, status, items_synced, details, now_str),
        )
        conn.commit()
    return {
        "worker": worker_name,
        "status": status,
        "items_synced": items_synced,
        "details": details,
        "executed_at": now_str,
    }


# =====================================================================
# 1. WORKER DO DISCORD (REST API v10 -> SQLite `messages_cache`)
# =====================================================================

class DiscordSyncWorker:
    """Sincroniza mensagens recentes de canais do Discord para `messages_cache`."""

    WORKER_NAME = "discord"

    async def sync(self) -> Dict[str, Any]:
        if settings.STRICT_OFFLINE_MODE:
            return _log_sync_event(
                self.WORKER_NAME,
                "OFFLINE_SKIPPED",
                0,
                "Kill-Switch Modo Estritamente Offline ativo. Conexão com Discord bloqueada.",
            )

        creds = _read_env_credentials()
        bot_token = (creds.get("DISCORD_BOT_TOKEN") or settings.DISCORD_BOT_TOKEN).strip()
        guild_id = (creds.get("DISCORD_GUILD_ID") or settings.DISCORD_GUILD_ID ).strip()
        channel_id = (creds.get("DISCORD_CHANNEL_ID") or settings.DISCORD_CHANNEL_ID ).strip()

        if not bot_token:
            return _log_sync_event(
                self.WORKER_NAME,
                "UNCONFIGURED",
                0,
                "DISCORD_BOT_TOKEN ausente no .env. Configure na aba 03 do Painel Desktop.",
            )

        headers = {"Authorization": f"Bot {bot_token}"}
        now_str = get_now_iso()
        synced_count = 0

        try:
            from workers.discord_bot import _get_companion_bridge_token, sophy_discord_service

            async with httpx.AsyncClient(timeout=8.0) as client:
                if channel_id:
                    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=20"
                    resp = await client.get(url, headers=headers)
                    bridge_used = False
                    if resp.status_code != 200:
                        comp_token = _get_companion_bridge_token()
                        if comp_token:
                            resp = await client.get(url, headers={"Authorization": f"Bot {comp_token}"})
                            bridge_used = resp.status_code == 200

                    if resp.status_code == 200:
                        messages = resp.json()
                        with db_manager.get_connection(read_only=False) as conn:
                            for msg in messages:
                                ext_id = f"disc-{msg.get('id')}"
                                author_obj = msg.get("author") or {}
                                author = author_obj.get("global_name") or author_obj.get("username", "unknown")
                                content = msg.get("content", "")
                                mentions = 1 if msg.get("mentions") else 0
                                sent_at = msg.get("timestamp", now_str)[:19].replace("T", " ")
                                conn.execute(
                                    """
                                    INSERT OR REPLACE INTO messages_cache
                                    (external_id, platform, channel_or_chat, sender_name, content, is_mention, is_replied, sent_at, synced_at)
                                    VALUES (?, 'discord', ?, ?, ?, ?, 0, ?, ?)
                                    """,
                                    (ext_id, f"channel:{channel_id}", author, content, mentions, sent_at, now_str),
                                )
                                synced_count += 1
                            conn.commit()

                        await sophy_discord_service.check_and_reply_unanswered_in_target_channel()
                        mode_tag = "Ponte Webhook Ativa" if bridge_used else "Gateway Nativo"
                        return _log_sync_event(
                            self.WORKER_NAME,
                            "OK",
                            synced_count,
                            f"{synced_count} mensagens sincronizadas do chat #{channel_id} (Servidor {guild_id} • {mode_tag}).",
                        )
                    return _log_sync_event(
                        self.WORKER_NAME,
                        "API_WARNING",
                        0,
                        f"Discord API retornou HTTP {resp.status_code} para o canal {channel_id}.",
                    )
                else:
                    # Valida apenas o token no endpoint /users/@me
                    me_resp = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
                    if me_resp.status_code == 200:
                        bot_user = me_resp.json().get("username", "Bot")
                        return _log_sync_event(
                            self.WORKER_NAME,
                            "OK",
                            0,
                            f"Autenticado no Discord como '{bot_user}'. Defina DISCORD_CHANNEL_ID para sincronizar mensagens.",
                        )
                    return _log_sync_event(
                        self.WORKER_NAME,
                        "AUTH_ERROR",
                        0,
                        f"Falha de autenticação Discord (HTTP {me_resp.status_code}).",
                    )
        except Exception as exc:
            return _log_sync_event(self.WORKER_NAME, "ERROR", 0, f"Erro de rede Discord: {exc}")


# =====================================================================
# 2. WORKER DO GMAIL (Google REST API -> SQLite `emails_cache`)
# =====================================================================

class GmailSyncWorker:
    """Sincroniza e-mails da caixa de entrada do Gmail para `emails_cache`."""

    WORKER_NAME = "gmail"

    async def sync(self) -> Dict[str, Any]:
        if settings.STRICT_OFFLINE_MODE:
            return _log_sync_event(
                self.WORKER_NAME,
                "OFFLINE_SKIPPED",
                0,
                "Kill-Switch Modo Estritamente Offline ativo. Conexão com Gmail bloqueada.",
            )

        creds = _read_env_credentials()
        client_id = creds.get("GOOGLE_CLIENT_ID", "").strip()
        access_token = creds.get("GOOGLE_ACCESS_TOKEN", "").strip()
        gws_token_enc = Path.home() / ".config" / "gws-cli" / "token.json.enc"
        gws_bin = Path.home() / ".local" / "bin" / "gws-cli"

        if gws_token_enc.exists() and gws_bin.exists():
            now_str = get_now_iso()
            try:
                import subprocess as _sp
                proc = await asyncio.to_thread(
                    _sp.run,
                    [str(gws_bin), "gmail", "list", "--max", "10"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout.strip())
                    msgs = data.get("messages") or data.get("items") or (data if isinstance(data, list) else [])
                    if isinstance(msgs, str):
                        try:
                            msgs = json.loads(msgs)
                        except Exception:
                            msgs = []
                    synced_count = 0
                    with db_manager.get_connection(read_only=False) as conn:
                        for m in msgs:
                            if not isinstance(m, dict):
                                continue
                            m_id = str(m.get("id", ""))
                            sender = str(m.get("from") or m.get("sender") or "desconhecido")
                            subject = str(m.get("subject") or "(Sem Assunto)")
                            snippet = str(m.get("snippet") or m.get("body") or "")
                            unread = 1 if m.get("unread", False) or "UNREAD" in (m.get("labelIds") or []) else 0
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO emails_cache
                                (external_id, sender, recipient, subject, snippet, body, category, is_unread, received_at, synced_at)
                                VALUES (?, ?, 'me', ?, ?, ?, 'inbox', ?, ?, ?)
                                """,
                                (f"gmail-{m_id}", sender, subject, snippet, snippet, unread, now_str, now_str),
                            )
                            synced_count += 1
                        conn.commit()
                    return _log_sync_event(
                        self.WORKER_NAME,
                        "OK",
                        synced_count,
                        f"{synced_count} e-mails sincronizados via gws-cli (OAuth2 ativo) para emails_cache.",
                    )
                else:
                    err_txt = (proc.stderr or proc.stdout).strip()[:160]
                    return _log_sync_event(self.WORKER_NAME, "API_WARNING", 0, f"gws-cli gmail: {err_txt}")
            except Exception as exc:
                return _log_sync_event(self.WORKER_NAME, "ERROR", 0, f"Erro gws-cli gmail: {exc}")

        if not client_id and not access_token:
            return _log_sync_event(
                self.WORKER_NAME,
                "UNCONFIGURED",
                0,
                "GOOGLE_CLIENT_ID / GOOGLE_ACCESS_TOKEN ausentes no .env. Mantendo cache SQLite local.",
            )

        if not access_token:
            return _log_sync_event(
                self.WORKER_NAME,
                "AWAITING_OAUTH_TOKEN",
                0,
                f"Client ID ({client_id[:12]}...) configurado. Aguardando token OAuth2 ativo.",
            )

        now_str = get_now_iso()
        synced_count = 0
        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10",
                    headers=headers,
                )
                if resp.status_code != 200:
                    return _log_sync_event(
                        self.WORKER_NAME,
                        "API_WARNING",
                        0,
                        f"Gmail API retornou HTTP {resp.status_code}.",
                    )

                msg_refs = resp.json().get("messages", [])
                with db_manager.get_connection(read_only=False) as conn:
                    for ref in msg_refs:
                        m_id = ref.get("id")
                        detail_resp = await client.get(
                            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m_id}?format=metadata",
                            headers=headers,
                        )
                        if detail_resp.status_code != 200:
                            continue
                        d_json = detail_resp.json()
                        snippet = d_json.get("snippet", "")
                        headers_list = (d_json.get("payload") or {}).get("headers", [])
                        hdr_map = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}
                        sender = hdr_map.get("from", "desconhecido")
                        subject = hdr_map.get("subject", "(Sem Assunto)")
                        unread = 1 if "UNREAD" in (d_json.get("labelIds") or []) else 0

                        conn.execute(
                            """
                            INSERT OR REPLACE INTO emails_cache
                            (external_id, sender, recipient, subject, snippet, body, category, is_unread, received_at, synced_at)
                            VALUES (?, ?, 'me', ?, ?, ?, 'inbox', ?, ?, ?)
                            """,
                            (f"gmail-{m_id}", sender, subject, snippet, snippet, unread, now_str, now_str),
                        )
                        synced_count += 1
                    conn.commit()

                return _log_sync_event(
                    self.WORKER_NAME,
                    "OK",
                    synced_count,
                    f"{synced_count} e-mails sincronizados do Gmail para emails_cache.",
                )
        except Exception as exc:
            return _log_sync_event(self.WORKER_NAME, "ERROR", 0, f"Erro de rede Gmail: {exc}")


# =====================================================================
# 3. WORKER DO GOOGLE DRIVE (Drive API -> Obsidian `/04_Drive_Sync/`)
# =====================================================================

class GoogleDriveSyncWorker:
    """Sincroniza documentos/notas do Google Drive para `OBSIDIAN_VAULT_PATH/04_Drive_Sync/`."""

    WORKER_NAME = "drive"

    async def sync(self) -> Dict[str, Any]:
        if settings.STRICT_OFFLINE_MODE:
            return _log_sync_event(
                self.WORKER_NAME,
                "OFFLINE_SKIPPED",
                0,
                "Kill-Switch Modo Estritamente Offline ativo. Conexão com Google Drive bloqueada.",
            )

        creds = _read_env_credentials()
        client_id = creds.get("GOOGLE_CLIENT_ID", "").strip()
        access_token = creds.get("GOOGLE_ACCESS_TOKEN", "").strip()

        drive_sync_dir = settings.OBSIDIAN_VAULT_PATH / "04_Drive_Sync"
        drive_sync_dir.mkdir(parents=True, exist_ok=True)
        gws_token_enc = Path.home() / ".config" / "gws-cli" / "token.json.enc"
        gws_bin = Path.home() / ".local" / "bin" / "gws-cli"

        if gws_token_enc.exists() and gws_bin.exists():
            try:
                import subprocess as _sp
                proc = await asyncio.to_thread(
                    _sp.run,
                    [str(gws_bin), "drive", "list", "--max", "15"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout.strip())
                    files = data.get("files") or data.get("items") or (data if isinstance(data, list) else [])
                    if isinstance(files, str):
                        try:
                            files = json.loads(files)
                        except Exception:
                            files = []
                    index_lines = [
                        "---",
                        "tags:",
                        "  - drive-sync",
                        "  - cloud-mirror",
                        f"updated_at: '{get_now_iso()}'",
                        "---",
                        "",
                        "# Catálogo Sincronizado do Google Drive",
                        "",
                    ]
                    for f in files:
                        if isinstance(f, dict):
                            index_lines.append(
                                f"- **{f.get('name')}** (`{f.get('mimeType', f.get('type', 'file'))}`) — ID: `{f.get('id', '')}`"
                            )
                    (drive_sync_dir / "Indice_Google_Drive.md").write_text(
                        "\n".join(index_lines) + "\n", encoding="utf-8"
                    )
                    return _log_sync_event(
                        self.WORKER_NAME,
                        "OK",
                        len(files),
                        f"Catálogo de {len(files)} arquivos do Drive espelhado via gws-cli em 04_Drive_Sync/Indice_Google_Drive.md.",
                    )
                else:
                    err_txt = (proc.stderr or proc.stdout).strip()[:160]
                    return _log_sync_event(self.WORKER_NAME, "API_WARNING", 0, f"gws-cli drive: {err_txt}")
            except Exception as exc:
                return _log_sync_event(self.WORKER_NAME, "ERROR", 0, f"Erro gws-cli drive: {exc}")

        if not client_id and not access_token:
            return _log_sync_event(
                self.WORKER_NAME,
                "UNCONFIGURED",
                0,
                "Credenciais do Google Drive não configuradas no .env. Pasta 04_Drive_Sync pronta localmente.",
            )

        if not access_token:
            return _log_sync_event(
                self.WORKER_NAME,
                "AWAITING_OAUTH_TOKEN",
                0,
                "Client ID Google presente; aguardando GOOGLE_ACCESS_TOKEN para espelhar arquivos no Vault.",
            )

        try:
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/files?pageSize=10&fields=files(id,name,mimeType,modifiedTime)",
                    headers=headers,
                )
                if resp.status_code != 200:
                    return _log_sync_event(
                        self.WORKER_NAME,
                        "API_WARNING",
                        0,
                        f"Google Drive API retornou HTTP {resp.status_code}.",
                    )

                files = resp.json().get("files", [])
                index_lines = [
                    "---",
                    "tags:",
                    "  - drive-sync",
                    "  - cloud-mirror",
                    f"updated_at: '{get_now_iso()}'",
                    "---",
                    "",
                    "# Catálogo Sincronizado do Google Drive",
                    "",
                ]
                for f in files:
                    index_lines.append(
                        f"- **{f.get('name')}** (`{f.get('mimeType')}`) — Modificado em: `{f.get('modifiedTime')}`"
                    )

                (drive_sync_dir / "Indice_Google_Drive.md").write_text(
                    "\n".join(index_lines) + "\n", encoding="utf-8"
                )
                return _log_sync_event(
                    self.WORKER_NAME,
                    "OK",
                    len(files),
                    f"Catálogo de {len(files)} arquivos do Drive espelhado em 04_Drive_Sync/Indice_Google_Drive.md.",
                )
        except Exception as exc:
            return _log_sync_event(self.WORKER_NAME, "ERROR", 0, f"Erro de rede Google Drive: {exc}")


# =====================================================================
# 4. WORKER DO WHATSAPP (Local Bridge -> SQLite `messages_cache`)
# =====================================================================

class WhatsAppSyncWorker:
    """Consome o bridge local do WhatsApp e grava mensagens em `messages_cache`."""

    WORKER_NAME = "whatsapp"

    async def sync(self) -> Dict[str, Any]:
        if settings.STRICT_OFFLINE_MODE:
            return _log_sync_event(
                self.WORKER_NAME,
                "OFFLINE_SKIPPED",
                0,
                "Kill-Switch Modo Estritamente Offline ativo. Sincronização do WhatsApp bloqueada.",
            )

        creds = _read_env_credentials()
        bridge_port = creds.get("WHATSAPP_BRIDGE_PORT", "3001").strip()
        bridge_url = f"http://127.0.0.1:{bridge_port}/messages/recent"
        now_str = get_now_iso()
        synced_count = 0

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(bridge_url)
                if resp.status_code == 200:
                    messages = resp.json().get("messages", [])
                    with db_manager.get_connection(read_only=False) as conn:
                        for m in messages:
                            ext_id = f"wpp-{m.get('id')}"
                            chat_name = m.get("chat", "Contato")
                            sender = m.get("sender", "Contato")
                            body = m.get("text", "")
                            mention = 1 if m.get("is_mention") else 0
                            sent_at = m.get("timestamp", now_str)
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO messages_cache
                                (external_id, platform, channel_or_chat, sender_name, content, is_mention, is_replied, sent_at, synced_at)
                                VALUES (?, 'whatsapp', ?, ?, ?, ?, 0, ?, ?)
                                """,
                                (ext_id, chat_name, sender, body, mention, sent_at, now_str),
                            )
                            synced_count += 1
                        conn.commit()
                    return _log_sync_event(
                        self.WORKER_NAME,
                        "OK",
                        synced_count,
                        f"{synced_count} mensagens sincronizadas do WhatsApp Bridge (porta {bridge_port}).",
                    )
                return _log_sync_event(
                    self.WORKER_NAME,
                    "BRIDGE_HTTP_WARN",
                    0,
                    f"Bridge WhatsApp na porta {bridge_port} respondeu HTTP {resp.status_code}.",
                )
        except httpx.ConnectError:
            return _log_sync_event(
                self.WORKER_NAME,
                "BRIDGE_STANDBY",
                0,
                f"Bridge WhatsApp (127.0.0.1:{bridge_port}) em standby. Usando cache SQLite local.",
            )
        except Exception as exc:
            return _log_sync_event(self.WORKER_NAME, "ERROR", 0, f"Erro no worker WhatsApp: {exc}")


# =====================================================================
# ORQUESTRADOR CENTRAL DE SINCRONIZAÇÃO (SYNC MANAGER)
# =====================================================================

class SyncManager:
    """Coordena a execução concorrente (`asyncio.gather`) ou individual dos 4 workers."""

    def __init__(self) -> None:
        self.workers = {
            "discord": DiscordSyncWorker(),
            "gmail": GmailSyncWorker(),
            "drive": GoogleDriveSyncWorker(),
            "whatsapp": WhatsAppSyncWorker(),
        }
        self._bg_task: Optional[asyncio.Task[None]] = None

    async def sync_selected(self, target: str = "all") -> Dict[str, Any]:
        """
        Executa a sincronização de um worker específico ('discord', 'gmail', 'drive', 'whatsapp')
        ou de todos simultaneamente ('all').
        """
        target_clean = target.strip().lower()
        if target_clean != "all":
            if target_clean not in self.workers:
                return {
                    "status": "INVALID_TARGET",
                    "valid_targets": ["all", *list(self.workers.keys())],
                }
            res = await self.workers[target_clean].sync()
            return {
                "strict_offline_mode": settings.STRICT_OFFLINE_MODE,
                "results": [res],
            }

        results = await asyncio.gather(*(w.sync() for w in self.workers.values()))
        return {
            "strict_offline_mode": settings.STRICT_OFFLINE_MODE,
            "results": list(results),
        }

    async def _periodic_loop(self, interval_seconds: int = 300) -> None:
        """Loop assíncrono em background que sincroniza a cada N segundos se não estiver em modo offline."""
        while True:
            try:
                if not settings.STRICT_OFFLINE_MODE:
                    await self.sync_selected("all")
            except Exception:
                pass
            await asyncio.sleep(interval_seconds)

    def start_background_loop(self, interval_seconds: int = 300) -> None:
        if self._bg_task is None or self._bg_task.done():
            self._bg_task = asyncio.create_task(self._periodic_loop(interval_seconds))

    def get_recent_sync_logs(self, limit: int = 12) -> List[Dict[str, Any]]:
        with db_manager.get_connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, worker_name, status, items_synced, details, executed_at
                FROM sync_status_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


sync_manager = SyncManager()


# =====================================================================
# TOOLS EXPOSTAS AO AGENTE (FASE 5)
# =====================================================================

async def trigger_online_sync(platform: str = "all") -> str:
    """
    Dispara a sincronização dos workers externos (Discord, Gmail, Google Drive, WhatsApp)
    para atualizar o cache SQLite local e o Vault do Obsidian.
    Se o botão de emergência `STRICT_OFFLINE_MODE` estiver ativo, bloqueia imediatamente
    qualquer tráfego de rede e retorna `OFFLINE_SKIPPED`.
    Parâmetro `platform`: 'all', 'discord', 'gmail', 'drive' ou 'whatsapp'.
    """
    report = await sync_manager.sync_selected(platform)
    return json.dumps(report, ensure_ascii=False, indent=2)


def get_sync_workers_status(limit: int = 10) -> str:
    """
    Consulta o histórico recente de execução dos workers de sincronização
    (Discord, Gmail, Drive e WhatsApp) registrado na tabela `sync_status_log` do SQLite.
    """
    logs = sync_manager.get_recent_sync_logs(limit=limit)
    return json.dumps(
        {
            "strict_offline_mode": settings.STRICT_OFFLINE_MODE,
            "recent_logs": logs,
        },
        ensure_ascii=False,
        indent=2,
    )


SYNC_TOOLS = [
    trigger_online_sync,
    get_sync_workers_status,
]
