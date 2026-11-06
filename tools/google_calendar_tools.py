"""Calendário do Google (conta real do usuário) via gws-cli.

Listar, criar, editar e apagar eventos diretamente no Google Calendar.
- Os argumentos vão como lista (sem montar comando em texto), então títulos com aspas são seguros.
- Apagar exige confirmação explícita: sem `confirmado=True`, a ferramenta só mostra o evento.
- Em modo estritamente offline, nada é enviado ao Google.
"""

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import settings

logger = logging.getLogger(__name__)

GWS_TIMEOUT = 45
_ISO_WITH_TZ = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _gws_bin() -> Optional[str]:
    found = shutil.which("gws-cli")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "gws-cli"
    return str(local) if local.exists() else None


def _run(args: List[str]) -> Dict[str, Any]:
    if settings.STRICT_OFFLINE_MODE:
        return {"ok": False, "erro": "Modo estritamente offline ativo: o Google Calendar não é acessado."}
    binary = _gws_bin()
    if not binary:
        return {"ok": False, "erro": "gws-cli não encontrado no sistema."}
    try:
        proc = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=GWS_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "erro": "O Google Calendar não respondeu a tempo."}
    except OSError as exc:
        return {"ok": False, "erro": f"Falha ao executar gws-cli: {exc}"}
    raw = (proc.stdout or "").strip()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"raw": raw[:500]}
    if proc.returncode != 0 or data.get("status") not in (None, "success"):
        detail = (proc.stderr or raw or "erro desconhecido").strip()[:400]
        return {"ok": False, "erro": f"Google Calendar recusou a operação: {detail}"}
    return {"ok": True, "dados": data}


def _iso(value: str, all_day: bool = False) -> str:
    """Normaliza data/hora. Sem fuso, usa o fuso da Sophy (America/Sao_Paulo)."""
    value = (value or "").strip()
    if not value:
        return value
    if all_day or _DATE_ONLY.match(value):
        return value[:10]
    if _ISO_WITH_TZ.search(value):
        return value
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    return parsed.replace(tzinfo=tz).isoformat(timespec="seconds")


def _format_event(ev: Dict[str, Any]) -> str:
    local = f" | local: {ev['location']}" if ev.get("location") else ""
    return f"- [{ev.get('id', '?')}] {ev.get('start', '?')} → {ev.get('end', '?')} | {ev.get('summary', '(sem título)')}{local}"


def listar_eventos_google(dias: int = 0, busca: str = "") -> str:
    """
    Lista os eventos do SEU Google Calendar nos próximos dias (padrão 14), incluindo o id de cada evento
    (necessário para editar ou apagar). Use quando o usuário perguntar quais são os eventos, compromissos,
    agenda ou o que está marcado. Para encontrar um evento pelo nome, use busca (procura em 1 ano).

    :param dias: quantos dias à frente listar (0 = automático: 14, ou 365 com busca).
    :param busca: texto opcional para filtrar pelo título ou descrição; quando informado, procura em 1 ano.
    """
    if not dias:
        dias = 365 if busca.strip() else 14
    dias = max(1, min(int(dias), 365))
    tz = ZoneInfo(settings.TIMEZONE)
    now = datetime.now(tz)
    inicio = now - timedelta(days=0 if not busca.strip() else 365)
    args = ["calendar", "list", "--from", inicio.isoformat(timespec="seconds"),
            "--to", (now + timedelta(days=dias)).isoformat(timespec="seconds"), "--max", "100"]
    if busca.strip():
        args += ["--query", busca.strip()]
    result = _run(args)
    if not result["ok"]:
        return json.dumps({"status": "ERRO", "message": result["erro"]}, ensure_ascii=False)
    raw_events = result["dados"].get("events", "[]")
    events = json.loads(raw_events) if isinstance(raw_events, str) else raw_events
    if not events:
        return f"Nenhum evento no Google Calendar nos próximos {dias} dia(s)."
    lines = [f"Google Calendar (conta principal), próximos {dias} dia(s): {len(events)} evento(s)."]
    lines += [_format_event(ev) for ev in events]
    return "\n".join(lines)


def criar_evento_google(
    titulo: str,
    inicio: str,
    fim: str,
    descricao: str = "",
    local: str = "",
    dia_inteiro: bool = False,
) -> str:
    """
    Cria um evento no SEU Google Calendar. Use quando o usuário pedir para marcar, agendar ou criar
    um compromisso ou evento. Datas: 'YYYY-MM-DDTHH:MM:SS' (sem fuso = horário de São Paulo),
    ou 'YYYY-MM-DD' com dia_inteiro=True.

    :param titulo: título do evento.
    :param inicio: início, ex: '2026-11-06T14:00:00'.
    :param fim: fim, ex: '2026-11-06T15:00:00'.
    :param descricao: detalhes opcionais.
    :param local: local ou link opcional.
    :param dia_inteiro: True para evento de dia inteiro (use datas YYYY-MM-DD).
    """
    if not titulo.strip() or not inicio or not fim:
        return json.dumps({"status": "ERRO", "message": "Informe título, início e fim."}, ensure_ascii=False)
    args = ["calendar", "create", titulo.strip(), _iso(inicio, dia_inteiro), _iso(fim, dia_inteiro)]
    if descricao:
        args += ["--description", descricao]
    if local:
        args += ["--location", local]
    if dia_inteiro:
        args += ["--all-day"]
    result = _run(args)
    if not result["ok"]:
        return json.dumps({"status": "ERRO", "message": result["erro"]}, ensure_ascii=False)
    dados = result["dados"]
    return json.dumps(
        {
            "status": "CRIADO",
            "id": dados.get("event_id") or dados.get("id") or dados.get("source_id"),
            "titulo": titulo,
            "inicio": _iso(inicio, dia_inteiro),
            "fim": _iso(fim, dia_inteiro),
            "link": dados.get("html_link") or dados.get("link", ""),
            "resposta_google": dados,
        },
        ensure_ascii=False,
        indent=2,
    )


def editar_evento_google(
    event_id: str,
    titulo: str = "",
    inicio: str = "",
    fim: str = "",
    descricao: str = "",
    local: str = "",
) -> str:
    """
    Edita um evento existente do SEU Google Calendar. Só altera os campos informados.
    Use o id que aparece em listar_eventos_google.

    :param event_id: id do evento (ex: '62qc33ljbfafi3162e6ckptv60_20261106T210000Z').
    :param titulo: novo título (opcional).
    :param inicio: novo início (opcional).
    :param fim: novo fim (opcional).
    :param descricao: nova descrição (opcional).
    :param local: novo local (opcional).
    """
    if not event_id.strip():
        return json.dumps({"status": "ERRO", "message": "Informe o id do evento (use listar_eventos_google)."}, ensure_ascii=False)
    args = ["calendar", "update", event_id.strip()]
    if titulo:
        args += ["--summary", titulo]
    if inicio:
        args += ["--start", _iso(inicio)]
    if fim:
        args += ["--end", _iso(fim)]
    if descricao:
        args += ["--description", descricao]
    if local:
        args += ["--location", local]
    if len(args) == 3:
        return json.dumps({"status": "ERRO", "message": "Nenhum campo para alterar foi informado."}, ensure_ascii=False)
    result = _run(args)
    if not result["ok"]:
        return json.dumps({"status": "ERRO", "message": result["erro"]}, ensure_ascii=False)
    return json.dumps({"status": "EDITADO", "id": event_id, "resposta_google": result["dados"]}, ensure_ascii=False, indent=2)


def apagar_evento_google(event_id: str, confirmado: bool = False) -> str:
    """
    Apaga um evento do SEU Google Calendar. É uma ação irreversível: primeiro chame sem `confirmado`
    para mostrar o evento ao usuário e pedir confirmação; só use confirmado=True depois que ele disser sim.

    :param event_id: id do evento (use listar_eventos_google).
    :param confirmado: True somente após o usuário confirmar a exclusão.
    """
    if not event_id.strip():
        return json.dumps({"status": "ERRO", "message": "Informe o id do evento."}, ensure_ascii=False)
    if not confirmado:
        return json.dumps(
            {
                "status": "AGUARDANDO_CONFIRMACAO",
                "id": event_id,
                "message": "Nada foi apagado. Mostre o evento ao usuário e peça confirmação explícita antes de apagar.",
            },
            ensure_ascii=False,
            indent=2,
        )
    result = _run(["calendar", "delete", event_id.strip()])
    if not result["ok"]:
        return json.dumps({"status": "ERRO", "message": result["erro"]}, ensure_ascii=False)
    return json.dumps({"status": "APAGADO", "id": event_id, "resposta_google": result["dados"]}, ensure_ascii=False, indent=2)


GOOGLE_CALENDAR_TOOLS = [
    listar_eventos_google,
    criar_evento_google,
    editar_evento_google,
    apagar_evento_google,
]
