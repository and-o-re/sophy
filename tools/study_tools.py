"""
Tutor de Estudos com Algoritmo de Repetição Espaçada SM-2 (SuperMemo-2) - FASE 3.
Persiste flashcards no SQLite (`study_cards` e `study_reviews_log`) e calcula
determinísticamente o Fator de Facilidade (Easiness Factor - EF), o intervalo em
dias e a data exata da próxima revisão (`next_review_date`).
"""

from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

from config import settings
from database.db_manager import db_manager, get_now_iso, get_today_str


def _calculate_sm2(
    quality: int,
    repetitions: int,
    easiness_factor: float,
    interval_days: int,
) -> tuple[int, float, int]:
    """
    Implementação matemática fiel do algoritmo SuperMemo-2 (SM-2):
    - `quality` (0 a 5):
      5: resposta perfeita imediata
      4: resposta correta após breve hesitação
      3: resposta correta com dificuldade
      2: resposta incorreta, mas a resposta certa parecia fácil de lembrar
      1: resposta incorreta, lembrou ao ver a resposta
      0: apagão completo
    """
    q = max(0, min(5, int(quality)))

    if q >= 3:
        if repetitions == 0:
            new_interval = 1
        elif repetitions == 1:
            new_interval = 6
        else:
            new_interval = max(1, round(interval_days * easiness_factor))
        new_repetitions = repetitions + 1
    else:
        new_repetitions = 0
        new_interval = 1

    new_ef = easiness_factor + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    if new_ef < 1.3:
        new_ef = 1.3

    return new_repetitions, round(new_ef, 4), int(new_interval)


# =====================================================================
# TOOLS EXPOSTAS AO AGENTE (TUTOR DE ESTUDOS SM-2)
# =====================================================================

def create_study_flashcard(
    deck: str,
    front: str,
    back: str,
    source_note: str = "",
) -> str:
    """
    Cria um novo flashcard de estudo no sistema de repetição espaçada SM-2.
    Parâmetros:
    - deck: Nome do baralho/matéria (ex: 'Arquitetura IA Local', 'Python Asyncio').
    - front: Pergunta ou conceito na frente do cartão.
    - back: Resposta concisa e precisa no verso do cartão.
    - source_note: Nota de origem no Obsidian (ex: 'Projetos/Arquitetura_Local.md').
    """
    today_str = get_today_str()
    now_str = get_now_iso()

    with db_manager.get_connection(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO study_cards
            (deck, front, back, source_note, repetitions, easiness_factor, interval_days, next_review_date, created_at)
            VALUES (?, ?, ?, ?, 0, 2.5, 0, ?, ?)
            """,
            (deck.strip(), front.strip(), back.strip(), source_note.strip() or None, today_str, now_str),
        )
        card_id = cur.lastrowid
        conn.commit()

    return json.dumps(
        {
            "status": "FLASHCARD_CREATED",
            "card_id": card_id,
            "deck": deck,
            "front": front,
            "next_review_date": today_str,
            "easiness_factor": 2.5,
        },
        ensure_ascii=False,
        indent=2,
    )


def get_due_study_flashcards(deck: str = "", limit: int = 10) -> str:
    """
    Lista os flashcards pendentes de revisão para hoje (ou atrasados), onde
    `next_review_date <= data_atual`.
    Use esta ferramenta quando o usuário pedir para estudar ou revisar flashcards.
    """
    today_str = get_today_str()
    with db_manager.get_connection(read_only=True) as conn:
        cur = conn.cursor()
        if deck.strip():
            cur.execute(
                """
                SELECT id, deck, front, back, source_note, repetitions, easiness_factor, interval_days, next_review_date
                FROM study_cards
                WHERE next_review_date <= ? AND LOWER(deck) LIKE LOWER(?)
                ORDER BY next_review_date ASC, id ASC
                LIMIT ?
                """,
                (today_str, f"%{deck.strip()}%", limit),
            )
        else:
            cur.execute(
                """
                SELECT id, deck, front, back, source_note, repetitions, easiness_factor, interval_days, next_review_date
                FROM study_cards
                WHERE next_review_date <= ?
                ORDER BY next_review_date ASC, id ASC
                LIMIT ?
                """,
                (today_str, limit),
            )
        cards = [dict(r) for r in cur.fetchall()]

    return json.dumps(
        {
            "status": "OK",
            "reference_date": today_str,
            "due_count": len(cards),
            "due_cards": cards,
        },
        ensure_ascii=False,
        indent=2,
    )


def review_study_flashcard_sm2(card_id: int, quality: int) -> str:
    """
    Registra a revisão de um flashcard aplicando o algoritmo SuperMemo-2 (SM-2).
    Parâmetros:
    - card_id: ID numérico do flashcard na tabela `study_cards`.
    - quality: Nota de desempenho de 0 a 5 (0=erro total, 3=acertou com esforço, 5=perfeito).
    Atualiza `repetitions`, `easiness_factor`, `interval_days` e `next_review_date` no SQLite.
    """
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")

    now_dt = datetime.now(tz)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    with db_manager.get_connection(read_only=False) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, deck, front, repetitions, easiness_factor, interval_days FROM study_cards WHERE id = ?;",
            (card_id,),
        )
        row = cur.fetchone()
        if not row:
            return json.dumps(
                {"status": "NOT_FOUND", "message": f"Flashcard com id={card_id} não existe."},
                ensure_ascii=False,
            )

        old_reps = int(row["repetitions"])
        old_ef = float(row["easiness_factor"])
        old_interval = int(row["interval_days"])

        new_reps, new_ef, new_interval = _calculate_sm2(
            quality=quality,
            repetitions=old_reps,
            easiness_factor=old_ef,
            interval_days=old_interval,
        )
        next_date_str = (now_dt + timedelta(days=new_interval)).strftime("%Y-%m-%d")

        cur.execute(
            """
            UPDATE study_cards
            SET repetitions = ?, easiness_factor = ?, interval_days = ?, next_review_date = ?, last_reviewed_at = ?
            WHERE id = ?
            """,
            (new_reps, new_ef, new_interval, next_date_str, now_str, card_id),
        )
        cur.execute(
            """
            INSERT INTO study_reviews_log
            (card_id, quality, old_ef, new_ef, new_interval_days, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (card_id, int(quality), old_ef, new_ef, new_interval, now_str),
        )
        conn.commit()

    return json.dumps(
        {
            "status": "SM2_UPDATED",
            "card_id": card_id,
            "deck": row["deck"],
            "quality_grade": int(quality),
            "previous_state": {
                "repetitions": old_reps,
                "easiness_factor": old_ef,
                "interval_days": old_interval,
            },
            "new_state": {
                "repetitions": new_reps,
                "easiness_factor": new_ef,
                "interval_days": new_interval,
                "next_review_date": next_date_str,
            },
        },
        ensure_ascii=False,
        indent=2,
    )


STUDY_TOOLS = [
    create_study_flashcard,
    get_due_study_flashcards,
    review_study_flashcard_sm2,
]
