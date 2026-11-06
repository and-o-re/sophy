"""Ferramentas do diário da Sophy (03_Diario no Obsidian)."""

from core import diario


def escrever_relatorio_no_diario(texto: str) -> str:
    """
    Escreve o relatório do dia no diário da Sophy (bloco "Relatório do dia" da nota de hoje).
    Use quando o usuário pedir um resumo, relatório ou registro do que foi feito.

    :param texto: relatório em Markdown: o que foi feito, comandos principais, o que ajudou e o que mudou.
    """
    if not texto or len(texto.strip()) < 20:
        return "Relatório vazio: escreva pelo menos uma frase de resumo."
    path = diario.escrever_relatorio(texto)
    return f"Relatório gravado no diário de hoje: {path}"


def ver_diario_de_hoje() -> str:
    """Mostra a nota do diário de hoje (comandos usados, ajuda prestada e alterações)."""
    path = diario.render_diary()
    return path.read_text(encoding="utf-8")


DIARIO_TOOLS = [escrever_relatorio_no_diario, ver_diario_de_hoje]
