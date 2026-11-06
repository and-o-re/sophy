"""Ponte do ecossistema: Sophy conversa com Sophya e Tanya.

Ferramentas síncronas e leves (biblioteca padrão), executadas no thread pool do
agente. Nenhuma delas imprime segredos; apenas mensagens e status.
"""

import json
import sys
from pathlib import Path
from typing import List

AI_ROOT = Path(__file__).resolve().parents[2]
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from ecossistema import (  # noqa: E402
    format_status,
    health,
    mark_read,
    read_messages,
    send_audit_request,
    send_message,
    status_all,
)


def _json(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def ecosystem_status() -> str:
    """Mostra o estado das três IAs (Sophy, Sophya, Tanya), processos e Ollama.

    Use quando o usuário perguntar se as IAs estão online, conectadas ou prontas.
    """
    return _json(status_all())


def ecosystem_status_text() -> str:
    """Mostra o status do ecossistema em texto legível, com uma linha por IA."""
    return format_status(status_all())


def ecosystem_send_message(destino: str, assunto: str, corpo: str, tipo: str = "mensagem") -> str:
    """Envia uma mensagem para outra IA do ecossistema.

    :param destino: "sophya", "tanya" ou "all".
    :param assunto: resumo curto da mensagem.
    :param corpo: conteúdo completo.
    :param tipo: mensagem, pedido_auditoria, resultado_auditoria, status ou alerta.
    """
    record = send_message("sophy", destino, assunto, corpo, kind=tipo)
    return _json({"status": "enviado", "id": record["id"], "para": record["para"], "assunto": record["assunto"]})


def ecosystem_read_messages(todas: bool = False, marcar_como_lidas: bool = False, limite: int = 20) -> str:
    """Lê a caixa de entrada da Sophy (mensagens de Sophya e Tanya).

    :param todas: se True, inclui mensagens já lidas.
    :param marcar_como_lidas: marca as mensagens retornadas como lidas.
    :param limite: número máximo de mensagens.
    """
    messages = read_messages(
        "sophy",
        unread_only=not todas,
        mark_as_read=marcar_como_lidas,
        limit=max(1, min(int(limite), 100)),
    )
    return _json(messages)


def ecosystem_mark_message_read(message_id: str) -> str:
    """Marca uma mensagem específica da caixa da Sophy como lida."""
    changed = mark_read("sophy", message_id=message_id)
    return _json({"status": "ok", "marcadas": changed, "id": message_id})


def ecosystem_request_audit(escopo: str = "ecossistema", detalhes: str = "", caminhos: List[str] = None) -> str:
    """Pede à Tanya uma auditoria de código, vault ou configuração.

    :param escopo: nome curto do escopo (ex.: "vault", "tools", "ecossistema").
    :param detalhes: o que deve ser verificado.
    :param caminhos: lista opcional de arquivos/diretórios relativos à pasta-mãe dos projetos.
    """
    record = send_audit_request(
        sender="sophy",
        target="tanya",
        scope=escopo,
        details=detalhes,
        paths=list(caminhos) if caminhos else None,
    )
    return _json({"status": "pedido_enviado", "id": record["id"], "escopo": escopo, "para": "tanya"})


def ecosystem_health() -> str:
    """Executa o autoteste da ponte compartilhada e da conexão com o Ollama."""
    return _json(health())


ECOSYSTEM_TOOLS = [
    ecosystem_status,
    ecosystem_status_text,
    ecosystem_send_message,
    ecosystem_read_messages,
    ecosystem_mark_message_read,
    ecosystem_request_audit,
    ecosystem_health,
]
