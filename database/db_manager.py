"""
Gerenciador do Banco de Dados SQLite Local (FASE 3 - Arquitetura Offline-First).
Armazena localmente:
- Cache offline de e-mails (`emails_cache`)
- Cache offline de mensagens do Discord e WhatsApp (`messages_cache`)
- Eventos de calendário e agendamentos (`calendar_events`)
- Flashcards de repetição espaçada com algoritmo SuperMemo-2 (`study_cards`, `study_reviews_log`)
- Log de auditoria de sincronização (`sync_status_log`)
"""

from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import settings


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS emails_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,
    sender TEXT NOT NULL,
    recipient TEXT,
    subject TEXT NOT NULL,
    snippet TEXT,
    body TEXT,
    category TEXT DEFAULT 'inbox',
    is_unread INTEGER DEFAULT 1,
    received_at TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,
    platform TEXT NOT NULL,          -- 'discord', 'whatsapp', etc.
    channel_or_chat TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    content TEXT NOT NULL,
    is_mention INTEGER DEFAULT 0,
    is_replied INTEGER DEFAULT 0,
    sent_at TEXT NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    start_time TEXT NOT NULL,        -- ISO-8601 (YYYY-MM-DD HH:MM:SS)
    end_time TEXT,
    location_or_link TEXT,
    source TEXT DEFAULT 'local',     -- 'local', 'obsidian', 'google_calendar'
    status TEXT DEFAULT 'confirmed', -- 'confirmed', 'completed', 'cancelled'
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck TEXT NOT NULL,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    source_note TEXT,                -- Ex: 'Projetos/Arquitetura_Local.md'
    repetitions INTEGER DEFAULT 0,
    easiness_factor REAL DEFAULT 2.5,
    interval_days INTEGER DEFAULT 0,
    next_review_date TEXT NOT NULL,  -- YYYY-MM-DD
    last_reviewed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_reviews_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    quality INTEGER NOT NULL,        -- Nota SM-2 de 0 a 5
    old_ef REAL NOT NULL,
    new_ef REAL NOT NULL,
    new_interval_days INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY (card_id) REFERENCES study_cards (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_status_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_name TEXT NOT NULL,       -- 'gmail', 'discord', 'whatsapp', 'drive'
    status TEXT NOT NULL,            -- 'OK', 'OFFLINE_SKIPPED', 'ERROR'
    items_synced INTEGER DEFAULT 0,
    details TEXT,
    executed_at TEXT NOT NULL
);
"""


def get_now_iso() -> str:
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")


def get_today_str() -> str:
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).strftime("%Y-%m-%d")


class DatabaseManager:
    """Gerencia conexões SQLite (leitura/escrita e modo estrito read-only para Text-to-SQL)."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path: Path = (db_path or settings.SQLITE_DB_PATH).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self, read_only: bool = False) -> sqlite3.Connection:
        """
        Abre uma conexão SQLite.
        Quando `read_only=True`, utiliza URI `mode=ro` e `PRAGMA query_only = ON`
        para bloquear qualquer mutação ao nível do motor do SQLite.
        """
        if read_only:
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            conn.execute("PRAGMA query_only = ON;")
        else:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Cria todas as tabelas caso não existam e popula dados iniciais se o banco for novo."""
        with self.get_connection(read_only=False) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        self.seed_initial_data_if_empty()

    def seed_initial_data_if_empty(self) -> None:
        """
        Popula registros iniciais realistas de cache offline (e-mails, mensagens,
        calendário e flashcards) apenas se o banco estiver vazio, permitindo testar
        imediatamente consultas SQL e revisão espaçada SM-2.
        """
        now_str = get_now_iso()
        today_str = get_today_str()

        with self.get_connection(read_only=False) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS cnt FROM emails_cache;")
            if cur.fetchone()["cnt"] == 0:
                conn.executemany(
                    """
                    INSERT INTO emails_cache
                    (external_id, sender, recipient, subject, snippet, body, category, is_unread, received_at, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "msg-001",
                            "alertas@github.com",
                            "user@local.ai",
                            "[GitHub] Security vulnerability alert in dependency",
                            "Atualização recomendada para pacote webview.",
                            "Detectamos um aviso de segurança informativo no repositório.",
                            "work",
                            1,
                            now_str,
                            now_str,
                        ),
                        (
                            "msg-002",
                            "equipe@projeto.dev",
                            "user@local.ai",
                            "Reunião de Arquitetura Offline-First",
                            "Pauta sobre integração FastAPI + Obsidian PKM.",
                            "Olá! Confirmamos a revisão da arquitetura modular hoje.",
                            "work",
                            1,
                            now_str,
                            now_str,
                        ),
                        (
                            "msg-003",
                            "newsletter@python.org",
                            "user@local.ai",
                            "Python Weekly: Asyncio & Local Agents",
                            "Destaques da semana sobre agentes locais com Ollama.",
                            "Artigos sobre otimização de VRAM em GPUs de 6GB.",
                            "newsletter",
                            0,
                            now_str,
                            now_str,
                        ),
                    ],
                )

            cur.execute("SELECT COUNT(*) AS cnt FROM messages_cache;")
            if cur.fetchone()["cnt"] == 0:
                conn.executemany(
                    """
                    INSERT INTO messages_cache
                    (external_id, platform, channel_or_chat, sender_name, content, is_mention, is_replied, sent_at, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "disc-101",
                            "discord",
                            "#engenharia-ia",
                            "Dev Lead",
                            "@user já subiu o modelo qwen2.5:7b-instruct-q4_K_M na GPU local?",
                            1,
                            0,
                            now_str,
                            now_str,
                        ),
                        (
                            "wpp-201",
                            "whatsapp",
                            "Grupo Pesquisa IA",
                            "Mariana",
                            "O teste com nomic-embed-text no ChromaDB ficou super rápido!",
                            0,
                            1,
                            now_str,
                            now_str,
                        ),
                        (
                            "wpp-202",
                            "whatsapp",
                            "Cliente Projeto",
                            "Carlos",
                            "Bom dia! Consegue gerar o relatório SQL ainda hoje?",
                            1,
                            0,
                            now_str,
                            now_str,
                        ),
                    ],
                )

            cur.execute("SELECT COUNT(*) AS cnt FROM calendar_events;")
            if cur.fetchone()["cnt"] == 0:
                conn.executemany(
                    """
                    INSERT INTO calendar_events
                    (title, description, start_time, end_time, location_or_link, source, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "Validação da Fase 3 (SQL + RAG + SM-2)",
                            "Testar Zero Alucinação Matemática com run_sql_report e embeddings locais.",
                            f"{today_str} 16:00:00",
                            f"{today_str} 17:00:00",
                            "seu notebook - Local",
                            "obsidian",
                            "confirmed",
                            now_str,
                        ),
                        (
                            "Desenvolvimento da GUI FastAPI (Fase 4)",
                            "Construir painel de configurações em 127.0.0.1:8080.",
                            f"{today_str} 18:00:00",
                            f"{today_str} 19:30:00",
                            "Localhost",
                            "local",
                            "confirmed",
                            now_str,
                        ),
                    ],
                )

            cur.execute("SELECT COUNT(*) AS cnt FROM study_cards;")
            if cur.fetchone()["cnt"] == 0:
                conn.executemany(
                    """
                    INSERT INTO study_cards
                    (deck, front, back, source_note, repetitions, easiness_factor, interval_days, next_review_date, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "Arquitetura IA Local",
                            "Qual parâmetro do Ollama garante que o modelo permaneça 5 minutos na VRAM da RTX 4050?",
                            "O parâmetro `keep_alive: '5m'` aliado a `num_ctx: 4096`.",
                            "Projetos/Arquitetura_Local.md",
                            0,
                            2.5,
                            0,
                            today_str,
                            now_str,
                        ),
                        (
                            "Engenharia de Dados & Agentes",
                            "Como a arquitetura evita alucinação matemática em contagens e relatórios?",
                            "Executando exclusivamente queries SELECT read-only no SQLite via `run_sql_report(sql_query)`.",
                            "00_Persona/personalidade_principal.md",
                            0,
                            2.5,
                            0,
                            today_str,
                            now_str,
                        ),
                    ],
                )

            conn.commit()

    def get_schema_summary(self) -> List[Dict[str, Any]]:
        """Retorna a lista de tabelas e colunas do SQLite para orientar o modelo em Text-to-SQL."""
        tables_info: List[Dict[str, Any]] = []
        with self.get_connection(read_only=True) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
            )
            table_names = [row["name"] for row in cur.fetchall()]
            for t_name in table_names:
                cur.execute(f"PRAGMA table_info({t_name});")
                cols = [f"{r['name']} ({r['type']})" for r in cur.fetchall()]
                cur.execute(f"SELECT COUNT(*) AS total FROM {t_name};")
                total_rows = cur.fetchone()["total"]
                tables_info.append(
                    {
                        "table": t_name,
                        "row_count": total_rows,
                        "columns": cols,
                    }
                )
        return tables_info


db_manager = DatabaseManager()
