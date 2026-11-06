"""
Ferramentas Nativas do WhatsApp MCP (`lharries/whatsapp-mcp`).
Conecta-se ao banco SQLite do `whatsapp-bridge` (`external_skills/whatsapp-mcp/whatsapp-bridge/store/messages.db`)
e à API REST local do bridge (`http://127.0.0.1:3001/api/...`), além de gerenciar o processo
em segundo plano do binário Go `whatsapp-bridge` (whatsmeow).
"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

from config import BASE_DIR, settings

WHATSAPP_MCP_ROOT = BASE_DIR / "external_skills" / "whatsapp-mcp"
WHATSAPP_BRIDGE_DIR = WHATSAPP_MCP_ROOT / "whatsapp-bridge"
WHATSAPP_BRIDGE_BIN = WHATSAPP_BRIDGE_DIR / "whatsapp-bridge"
WHATSAPP_STORE_DIR = WHATSAPP_BRIDGE_DIR / "store"
WHATSAPP_MESSAGES_DB = WHATSAPP_STORE_DIR / "messages.db"
WHATSAPP_SESSION_DB = WHATSAPP_STORE_DIR / "whatsapp.db"
WHATSAPP_MCP_SERVER_DIR = WHATSAPP_MCP_ROOT / "whatsapp-mcp-server"

if str(WHATSAPP_MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(WHATSAPP_MCP_SERVER_DIR))

_bridge_proc: Optional[subprocess.Popen] = None


def ensure_whatsapp_bridge_running() -> Dict[str, Any]:
    """
    Garante que o binário Go `whatsapp-bridge` esteja rodando em segundo plano na porta
    `settings.WHATSAPP_BRIDGE_PORT` (padrão 3001) para fornecer `/api/status`, `/api/qr`,
    `/api/send` e `/messages/recent`.
    """
    global _bridge_proc
    port = settings.WHATSAPP_BRIDGE_PORT or 3001
    status_url = f"http://127.0.0.1:{port}/api/status"

    # Verifica se já responde na porta
    try:
        with httpx.Client(timeout=1.5) as client:
            resp = client.get(status_url)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    if not WHATSAPP_BRIDGE_BIN.exists():
        return {
            "status": "BINARY_MISSING",
            "paired": False,
            "connected": False,
            "error": f"Binário não encontrado em {WHATSAPP_BRIDGE_BIN}",
        }

    WHATSAPP_STORE_DIR.mkdir(parents=True, exist_ok=True)
    log_file = WHATSAPP_BRIDGE_DIR / "bridge.log"
    env = os.environ.copy()
    env["WHATSAPP_BRIDGE_PORT"] = str(port)

    if _bridge_proc is None or _bridge_proc.poll() is not None:
        log_fh = open(log_file, "a", encoding="utf-8")
        _bridge_proc = subprocess.Popen(
            [str(WHATSAPP_BRIDGE_BIN)],
            cwd=str(WHATSAPP_BRIDGE_DIR),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    # Aguarda até 3.5s para o servidor REST inicializar e gerar o QR code se não pareado
    for _ in range(7):
        time.sleep(0.5)
        try:
            with httpx.Client(timeout=1.5) as client:
                resp = client.get(status_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("qr_code") or data.get("paired"):
                        return data
        except Exception:
            continue

    try:
        with httpx.Client(timeout=1.5) as client:
            resp = client.get(status_url)
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        return {
            "status": "STARTING",
            "paired": False,
            "connected": False,
            "bridge_port": port,
            "details": str(exc),
        }
    return {"status": "STARTING", "paired": False, "connected": False, "bridge_port": port}


def whatsapp_bridge_status() -> str:
    """
    [Skill: whatsapp-mcp] Verifica o status em tempo real da ponte WhatsApp (`whatsapp-bridge`),
    incluindo se o celular está pareado, JID conectado, porta da ponte e contagem de conversas/mensagens
    no banco SQLite do WhatsApp.
    """
    state = ensure_whatsapp_bridge_running()
    chats_count = 0
    msgs_count = 0
    if WHATSAPP_MESSAGES_DB.exists():
        try:
            conn = sqlite3.connect(str(WHATSAPP_MESSAGES_DB))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM chats")
            chats_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM messages")
            msgs_count = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

    state["sqlite_chats_count"] = chats_count
    state["sqlite_messages_count"] = msgs_count
    state["messages_db_path"] = str(WHATSAPP_MESSAGES_DB)
    return json.dumps(state, ensure_ascii=False, indent=2)


def whatsapp_list_chats(query: str = "", limit: int = 15) -> str:
    """
    [Skill: whatsapp-mcp] Lista as conversas (individuais e grupos) do WhatsApp armazenadas
    no banco do `whatsapp-bridge`, ordenadas pela última mensagem.
    Parâmetros:
    - query: Filtro opcional por nome do contato/grupo ou JID.
    - limit: Quantidade máxima de conversas a retornar (padrão 15).
    """
    ensure_whatsapp_bridge_running()
    results: List[Dict[str, Any]] = []
    if WHATSAPP_MESSAGES_DB.exists():
        try:
            conn = sqlite3.connect(str(WHATSAPP_MESSAGES_DB))
            cur = conn.cursor()
            sql = """
                SELECT c.jid, c.name, c.last_message_time,
                       (SELECT content FROM messages m WHERE m.chat_jid = c.jid ORDER BY m.timestamp DESC LIMIT 1) as last_msg
                FROM chats c
            """
            params: List[Any] = []
            if query.strip():
                sql += " WHERE LOWER(c.name) LIKE ? OR LOWER(c.jid) LIKE ?"
                q = f"%{query.strip().lower()}%"
                params.extend([q, q])
            sql += " ORDER BY c.last_message_time DESC LIMIT ?"
            params.append(int(limit))
            cur.execute(sql, params)
            for row in cur.fetchall():
                results.append(
                    {
                        "jid": row[0],
                        "name": row[1] or row[0],
                        "last_message_time": row[2],
                        "last_message_preview": row[3] or "",
                    }
                )
            conn.close()
        except Exception as exc:
            return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)

    return json.dumps(
        {
            "status": "OK",
            "count": len(results),
            "chats": results,
        },
        ensure_ascii=False,
        indent=2,
    )


def whatsapp_list_messages(
    chat_jid: str = "",
    query: str = "",
    limit: int = 20,
) -> str:
    """
    [Skill: whatsapp-mcp] Lista ou pesquisa mensagens no histórico do WhatsApp (`whatsapp-bridge` + cache local).
    Parâmetros:
    - chat_jid: Filtra por um JID específico (ex: '5511999999999@s.whatsapp.net' ou grupo '@g.us').
    - query: Termo opcional para buscar no texto das mensagens.
    - limit: Quantidade máxima de mensagens (padrão 20).
    """
    ensure_whatsapp_bridge_running()
    messages: List[Dict[str, Any]] = []
    if WHATSAPP_MESSAGES_DB.exists():
        try:
            conn = sqlite3.connect(str(WHATSAPP_MESSAGES_DB))
            cur = conn.cursor()
            sql = """
                SELECT m.id, m.chat_jid, COALESCE(c.name, m.sender, m.chat_jid), m.sender,
                       m.content, m.timestamp, m.is_from_me, m.media_type
                FROM messages m
                LEFT JOIN chats c ON m.chat_jid = c.jid
                WHERE 1=1
            """
            params: List[Any] = []
            if chat_jid.strip():
                sql += " AND m.chat_jid = ?"
                params.append(chat_jid.strip())
            if query.strip():
                sql += " AND LOWER(m.content) LIKE ?"
                params.append(f"%{query.strip().lower()}%")
            sql += " ORDER BY m.timestamp DESC LIMIT ?"
            params.append(int(limit))
            cur.execute(sql, params)
            for r in cur.fetchall():
                messages.append(
                    {
                        "id": r[0],
                        "chat_jid": r[1],
                        "chat_name": r[2],
                        "sender": r[3],
                        "content": r[4],
                        "timestamp": r[5],
                        "is_from_me": bool(r[6]),
                        "media_type": r[7] or "",
                    }
                )
            conn.close()
        except Exception as exc:
            return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)

    return json.dumps(
        {
            "status": "OK",
            "count": len(messages),
            "messages": messages,
        },
        ensure_ascii=False,
        indent=2,
    )


def whatsapp_search_contacts(query: str) -> str:
    """
    [Skill: whatsapp-mcp] Pesquisa contatos no WhatsApp por nome ou número de telefone/JID.
    """
    return whatsapp_list_chats(query=query, limit=20)


def whatsapp_send_message(recipient: str, message: str) -> str:
    """
    [Skill: whatsapp-mcp] Envia uma mensagem de texto real pelo WhatsApp usando o `whatsapp-bridge` local.
    Parâmetros:
    - recipient: Número de telefone com DDI+DDD (ex: '5511999999999') ou JID completo ('5511999999999@s.whatsapp.net' ou grupo '@g.us').
    - message: Texto da mensagem a ser enviada.
    """
    if settings.STRICT_OFFLINE_MODE:
        return json.dumps(
            {"status": "BLOCKED_OFFLINE", "message": "Modo Estritamente Offline ativo."},
            ensure_ascii=False,
        )

    state = ensure_whatsapp_bridge_running()
    if not state.get("paired"):
        return json.dumps(
            {
                "status": "NOT_PAIRED",
                "message": (
                    "O WhatsApp ainda não está pareado! Escaneie o QR Code na Aba 03 do Painel Desktop "
                    "ou execute `external_skills/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge` dentro da pasta da Sophy."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    port = settings.WHATSAPP_BRIDGE_PORT or 3001
    url = f"http://127.0.0.1:{port}/api/send"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json={"recipient": recipient.strip(), "message": message})
            return json.dumps(
                {
                    "status": "SENT" if resp.status_code == 200 else "ERROR",
                    "http_status": resp.status_code,
                    "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
                },
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


def whatsapp_send_file(recipient: str, media_path: str, caption: str = "") -> str:
    """
    [Skill: whatsapp-mcp] Envia um arquivo de mídia (imagem, vídeo, documento ou áudio) pelo WhatsApp.
    Parâmetros:
    - recipient: Número de telefone ou JID.
    - media_path: Caminho absoluto do arquivo no computador.
    - caption: Legenda opcional.
    """
    if settings.STRICT_OFFLINE_MODE:
        return json.dumps({"status": "BLOCKED_OFFLINE"}, ensure_ascii=False)

    abs_path = str(Path(media_path).expanduser().resolve())
    if not Path(abs_path).exists():
        return json.dumps({"status": "FILE_NOT_FOUND", "path": abs_path}, ensure_ascii=False)

    ensure_whatsapp_bridge_running()
    port = settings.WHATSAPP_BRIDGE_PORT or 3001
    url = f"http://127.0.0.1:{port}/api/send"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                json={"recipient": recipient.strip(), "message": caption, "media_path": abs_path},
            )
            return json.dumps(
                {
                    "status": "SENT" if resp.status_code == 200 else "ERROR",
                    "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
                },
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


WHATSAPP_TOOLS = [
    whatsapp_bridge_status,
    whatsapp_list_chats,
    whatsapp_list_messages,
    whatsapp_search_contacts,
    whatsapp_send_message,
    whatsapp_send_file,
]
