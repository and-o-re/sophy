"""Diário da Sophy: uma nota por dia no Obsidian (03_Diario/AAAA-MM-DD.md).

A cada conversa, a Sophy registra:
- os comandos (ferramentas) que foram usados e o resultado;
- o que ela ajudou o usuário (pergunta e resumo da resposta);
- o que foi alterado (escritas, edições e criações de arquivos).

Os eventos ficam em database/diario_eventos.jsonl e a nota é regenerada a partir deles.
O bloco de relatório escrito pela Sophy fica entre marcadores e é preservado.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import BASE_DIR, settings

logger = logging.getLogger(__name__)

EVENTS_FILE = BASE_DIR / "database" / "diario_eventos.jsonl"
MARK_BEGIN = "<!-- relatorio-manual:inicio -->"
MARK_END = "<!-- relatorio-manual:fim -->"

MUTATING_MARKERS = (
    "write", "criar", "create", "editar", "edit", "apagar", "apaga", "delete", "remov", "apply", "append", "manage",
    "render", "enviar", "send", "review_study", "indexar", "index_", "sync",
    "activate", "auto_adjust", "control", "set_", "salvar", "save", "defuddle",
)
PATH_KEYS = ("relative_path", "caminho", "caminho_nota", "caminho_pasta", "file_path", "output_vault_note", "path")
SECRET_KEY_RE = re.compile(r"(key|token|secret|password|senha|credential)", re.IGNORECASE)


def _now() -> datetime:
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


def _is_mutating(tool_name: str) -> bool:
    name = tool_name.lower()
    return any(marker in name for marker in MUTATING_MARKERS)


def _summarize_args(args: Dict[str, Any]) -> str:
    parts = []
    for key, value in (args or {}).items():
        if SECRET_KEY_RE.search(key):
            parts.append(f"{key}=***")
            continue
        text = str(value).replace("\n", " ")
        if len(text) > 60:
            text = text[:57] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def _first_path(args: Dict[str, Any]) -> Optional[str]:
    for key in PATH_KEYS:
        value = (args or {}).get(key)
        if value:
            return str(value)
    return None


def _append_event(event: Dict[str, Any]) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _events_for(day: str) -> List[Dict[str, Any]]:
    if not EVENTS_FILE.is_file():
        return []
    items: List[Dict[str, Any]] = []
    for raw in EVENTS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("data") == day:
            items.append(event)
    return items


def diary_path(day: Optional[str] = None) -> Path:
    day = day or _now().strftime("%Y-%m-%d")
    return settings.OBSIDIAN_VAULT_PATH / "03_Diario" / f"{day}.md"


def registrar_turno(
    pergunta: str,
    resposta: str,
    ferramentas: List[Dict[str, Any]],
    provedor: str = "",
) -> None:
    """Registra uma conversa (pergunta, resposta, comandos usados) e atualiza a nota do dia."""
    now = _now()
    base = {"data": now.strftime("%Y-%m-%d"), "hora": now.strftime("%H:%M")}
    _append_event({**base, "tipo": "conversa", "pergunta": pergunta.strip()[:240],
                   "resposta": resposta.strip().replace("\n", " ")[:300], "provedor": provedor})
    for item in ferramentas:
        name = item.get("ferramenta", "?")
        args = item.get("args", {})
        _append_event({**base, "tipo": "ferramenta", "ferramenta": name,
                       "argumentos": _summarize_args(args), "ok": bool(item.get("ok"))})
        if _is_mutating(name) and item.get("ok"):
            _append_event({**base, "tipo": "alteracao", "ferramenta": name,
                           "caminho": _first_path(args) or "(sem caminho)"})
    try:
        render_diary(now.strftime("%Y-%m-%d"))
    except OSError as exc:
        logger.warning(f"Não consegui atualizar o diário: {exc}")


def _manual_block(existing: str) -> str:
    if MARK_BEGIN in existing and MARK_END in existing:
        start = existing.index(MARK_BEGIN) + len(MARK_BEGIN)
        end = existing.index(MARK_END)
        return existing[start:end].strip("\n")
    return ""


def render_diary(day: Optional[str] = None) -> Path:
    """Regenera a nota do dia a partir dos eventos, preservando o relatório escrito pela Sophy."""
    day = day or _now().strftime("%Y-%m-%d")
    path = diary_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    manual = _manual_block(path.read_text(encoding="utf-8")) if path.is_file() else ""

    events = _events_for(day)
    conversas = [e for e in events if e["tipo"] == "conversa"]
    ferramentas = [e for e in events if e["tipo"] == "ferramenta"]
    alteracoes = [e for e in events if e["tipo"] == "alteracao"]
    data_br = datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m/%Y")

    out = [
        "---",
        f"title: 'Diário da Sophy — {data_br}'",
        "tags:",
        "  - diario",
        "  - sophy",
        "  - automatico",
        f"updated_at: '{_now().strftime('%Y-%m-%d %H:%M:%S')}'",
        "---",
        "",
        f"# 📔 Diário da Sophy — {data_br}",
        "",
        "> Nota escrita pela própria Sophy. Os registros são automáticos a cada conversa; "
        "o bloco *Relatório do dia* é escrito por ela e preservado.",
        "",
        "## 📊 Resumo do dia",
        f"- Conversas: **{len(conversas)}**",
        f"- Comandos (ferramentas) usados: **{len(ferramentas)}**",
        f"- Alterações em arquivos: **{len(alteracoes)}**",
        "",
        "## 🛠️ Comandos usados",
    ]
    if ferramentas:
        out += ["| Hora | Comando | Argumentos | Resultado |", "|---|---|---|---|"]
        for e in ferramentas:
            arg = e.get("argumentos", "").replace("|", "/")
            out.append(f"| {e['hora']} | `{e['ferramenta']}` | {arg} | {'ok' if e.get('ok') else 'falhou'} |")
    else:
        out.append("Nenhum comando registrado hoje.")

    out += ["", "## 🤝 No que ajudei o usuário"]
    if conversas:
        for e in conversas:
            out.append(f"- **{e['hora']}** — pergunta: \"{e['pergunta']}\"")
            out.append(f"  - resposta: {e['resposta']}")
    else:
        out.append("Nenhuma conversa registrada hoje.")

    out += ["", "## ✏️ O que foi alterado"]
    if alteracoes:
        for e in alteracoes:
            out.append(f"- **{e['hora']}** `{e['ferramenta']}` → `{e['caminho']}`")
    else:
        out.append("Nenhuma alteração em arquivos registrada hoje.")

    out += ["", "## 📝 Relatório do dia", MARK_BEGIN, manual or "_Ainda sem relatório escrito para este dia._", MARK_END, ""]
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def escrever_relatorio(texto: str, day: Optional[str] = None) -> Path:
    """Grava o relatório escrito pela Sophya no bloco próprio da nota do dia."""
    day = day or _now().strftime("%Y-%m-%d")
    path = render_diary(day)
    content = path.read_text(encoding="utf-8")
    block = texto.strip()
    start = content.index(MARK_BEGIN) + len(MARK_BEGIN)
    end = content.index(MARK_END)
    content = content[:start] + "\n" + block + "\n" + content[end:]
    path.write_text(content, encoding="utf-8")
    return path
