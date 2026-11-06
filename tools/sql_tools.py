"""
Ferramentas de Relatórios Text-to-SQL e Zero Alucinação Matemática (FASE 3).
Qualquer contagem, soma, média, filtro ou relatório sobre e-mails, mensagens,
eventos de calendário ou flashcards é executado EXCLUSIVAMENTE via
`run_sql_report(sql_query)` em modo Read-Only no SQLite local.
"""

import json
import re
from typing import Optional

from database.db_manager import db_manager, get_now_iso

FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)


def inspect_sql_schema() -> str:
    """
    Retorna o esquema completo do banco de dados SQLite local (tabelas, colunas,
    tipos e quantidade total de linhas em cada tabela).
    Tabelas disponíveis: `emails_cache`, `messages_cache`, `calendar_events`,
    `study_cards`, `study_reviews_log`, `sync_status_log`.
    Use esta ferramenta antes de escrever uma query SQL caso tenha dúvida sobre o nome das colunas.
    """
    schema = db_manager.get_schema_summary()
    return json.dumps(
        {
            "status": "OK",
            "database_path": str(db_manager.db_path),
            "tables": schema,
        },
        ensure_ascii=False,
        indent=2,
    )


def run_sql_report(sql_query: str) -> str:
    """
    Executa uma consulta SQL `SELECT` estritamente READ-ONLY no banco SQLite local
    para produzir contagens exatas, agregações (`COUNT`, `SUM`, `AVG`, `GROUP BY`)
    e relatórios sem alucinação matemática.
    Tabelas principais e suas colunas:
    - `emails_cache`: id, external_id, sender, recipient, subject, snippet, body, category, is_unread (1/0), received_at, synced_at
    - `messages_cache`: id, external_id, platform ('discord'|'whatsapp'), channel_or_chat, sender_name, content, is_mention (1/0), is_replied (1/0), sent_at, synced_at
    - `calendar_events`: id, title, description, start_time, end_time, location_or_link, source, status, created_at
    - `study_cards`: id, deck, front, back, source_note, repetitions, easiness_factor, interval_days, next_review_date, last_reviewed_at, created_at
    - `study_reviews_log`: id, card_id, quality, old_ef, new_ef, new_interval_days, reviewed_at
    """
    cleaned_query = sql_query.strip().rstrip(";")

    if not cleaned_query.upper().startswith(("SELECT", "WITH")):
        return json.dumps(
            {
                "status": "REJECTED_NOT_SELECT",
                "message": "Apenas queries iniciadas com SELECT ou WITH são permitidas em run_sql_report.",
            },
            ensure_ascii=False,
        )

    if ";" in cleaned_query:
        return json.dumps(
            {
                "status": "REJECTED_MULTIPLE_STATEMENTS",
                "message": "Múltiplas instruções SQL separadas por ';' não são permitidas.",
            },
            ensure_ascii=False,
        )

    if FORBIDDEN_SQL_KEYWORDS.search(cleaned_query):
        return json.dumps(
            {
                "status": "REJECTED_MUTATION_KEYWORD",
                "message": "Palavras-chave de modificação de dados/esquema são estritamente bloqueadas.",
            },
            ensure_ascii=False,
        )

    with db_manager.get_connection(read_only=True) as conn:
        cur = conn.cursor()
        cur.execute(cleaned_query)
        rows = [dict(row) for row in cur.fetchall()]

    return json.dumps(
        {
            "status": "OK",
            "executed_query": cleaned_query,
            "row_count": len(rows),
            "rows": rows[:100],
        },
        ensure_ascii=False,
        indent=2,
    )


def listar_eventos_calendario(apenas_futuros: bool = False, limite: int = 20) -> str:
    """
    Lista os eventos e compromissos JÁ MARCADOS no calendário local (tabela `calendar_events`).
    Use quando o usuário perguntar quais são os eventos, compromissos, agenda ou o que está marcado.
    Para criar um evento novo, use schedule_local_calendar_event.

    :param apenas_futuros: se True, mostra só eventos com data de início a partir de agora.
    :param limite: número máximo de eventos (padrão 20).
    """
    limite = max(1, min(int(limite or 20), 100))
    filtro = "WHERE start_time >= datetime('now', 'localtime')" if apenas_futuros else ""
    with db_manager.get_connection(read_only=True) as conn:
        rows = conn.execute(
            f"SELECT id, title, start_time, end_time, location_or_link, source, status "
            f"FROM calendar_events {filtro} ORDER BY start_time ASC LIMIT ?",
            (limite,),
        ).fetchall()
    if not rows:
        return "Não há eventos marcados no calendário local (tabela calendar_events)."
    linhas = [f"Eventos no calendário local: {len(rows)} encontrado(s)."]
    for r in rows:
        fim = f" até {r['end_time']}" if r["end_time"] else ""
        linhas.append(
            f"- [{r['id']}] {r['start_time']}{fim} | {r['title']} | local: {r['location_or_link'] or '-'} "
            f"| origem: {r['source']} | status: {r['status']}"
        )
    linhas.append("Obs.: é o calendário local do banco da Sophy. Eventos do Google Calendar aparecem com gws_calendar_events.")
    return "\n".join(linhas)


def schedule_local_calendar_event(
    title: str,
    start_time: str,
    end_time: str = "",
    description: str = "",
    location_or_link: str = "Local",
) -> str:
    """
    Agenda um novo evento ou compromisso na tabela `calendar_events` do banco SQLite local.
    Parâmetros:
    - title: Título do evento.
    - start_time: Data e hora de início no formato 'YYYY-MM-DD HH:MM:SS'.
    - end_time: Data e hora de término opcional ('YYYY-MM-DD HH:MM:SS').
    - description: Detalhes ou pauta do evento.
    - location_or_link: Local físico ou link.
    """
    now_str = get_now_iso()
    with db_manager.get_connection(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO calendar_events
            (title, description, start_time, end_time, location_or_link, source, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'local', 'confirmed', ?)
            """,
            (title.strip(), description.strip(), start_time.strip(), end_time.strip() or None, location_or_link.strip(), now_str),
        )
        event_id = cur.lastrowid
        conn.commit()

    return json.dumps(
        {
            "status": "EVENT_SCHEDULED",
            "event_id": event_id,
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
        },
        ensure_ascii=False,
        indent=2,
    )


SQL_TOOLS = [
    inspect_sql_schema,
    run_sql_report,
    schedule_local_calendar_event,
    listar_eventos_calendario,
]
