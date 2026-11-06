"""Ferramentas de tutoria offline da Sophy.

- ler_pdf: lê páginas de um PDF (texto com camada de texto, via poppler `pdftotext`).
- buscar_no_pdf: encontra um termo no PDF e devolve as páginas com contexto.
- revisar_codigo_didatico: análise estática de um arquivo Python, com linhas e explicações
  para o estudante entender cada achado.

Tudo roda localmente, sem internet. Leitura só em ALLOWED_CODE_DIRS e READ_ONLY_DIRS.
"""

import ast
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from config import settings

MAX_CHARS_PER_CALL = 6000
MAX_PAGES_PER_CALL = 6
MAX_PDF_CHARS_SEARCH = 2_000_000
PDFTOTEXT_TIMEOUT = 60


def _resolve_readable(path_str: str) -> Path:
    candidate = Path(path_str.strip()).expanduser().resolve()
    for root in settings.readable_paths:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    raise PermissionError(
        f"Acesso de leitura negado a '{candidate}'. Permitido apenas em: "
        + ", ".join(str(p) for p in settings.readable_paths)
    )


def _pdf_pages(path: Path) -> int:
    try:
        out = subprocess.run(
            ["pdfinfo", str(path)], capture_output=True, text=True, timeout=20, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Não consegui abrir o PDF com pdfinfo: {exc}") from exc
    match = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(match.group(1)) if match else 0


def _extract_pages(path: Path, first: int, last: int) -> List[str]:
    """Devolve o texto de cada página do intervalo (1 item por página)."""
    result = subprocess.run(
        ["pdftotext", "-layout", "-f", str(first), "-l", str(last), str(path), "-"],
        capture_output=True,
        text=True,
        timeout=PDFTOTEXT_TIMEOUT,
        check=True,
    )
    pages = result.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def _check_pdf(path_str: str) -> Path:
    path = _resolve_readable(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError("Esta ferramenta lê apenas arquivos .pdf.")
    return path


def ler_pdf(caminho: str, pagina_inicial: int = 1, pagina_final: int = 0) -> str:
    """
    Lê páginas de um PDF local (apostilas, relatórios, livros, material de aula) e devolve o texto
    com marcadores de página. Use para entender o conteúdo antes de explicar. Leia em partes:
    se a resposta indicar 'continua', chame de novo a partir da próxima página.

    :param caminho: caminho completo do PDF (ex: ~/Documents/Estudos/arquivo.pdf).
    :param pagina_inicial: primeira página a ler (começa em 1).
    :param pagina_final: última página (0 = automático, até 6 páginas por chamada).
    """
    path = _check_pdf(caminho)
    total = _pdf_pages(path)
    if total == 0:
        return "PDF sem páginas legíveis."

    first = max(1, int(pagina_inicial or 1))
    if first > total:
        return f"O PDF tem {total} página(s); a página {first} não existe."
    last = int(pagina_final) if pagina_final else first + MAX_PAGES_PER_CALL - 1
    last = min(total, last, first + MAX_PAGES_PER_CALL - 1)

    pages = _extract_pages(path, first, last)
    if not any(p.strip() for p in pages):
        return (
            f"'{path.name}' não tem camada de texto nas páginas {first}-{last} "
            f"(provavelmente é um PDF escaneado). OCR (tesseract) não está instalado, "
            f"então não consigo ler essas páginas."
        )

    chunks: List[str] = []
    size = 0
    shown_last = first - 1
    for offset, text in enumerate(pages):
        number = first + offset
        block = f"--- Página {number} de {total} ---\n{text.rstrip()}\n"
        if size + len(block) > MAX_CHARS_PER_CALL and chunks:
            break
        chunks.append(block)
        size += len(block)
        shown_last = number

    header = f"PDF: {path.name} | {total} página(s) | lendo {first}-{shown_last}"
    footer = ""
    if shown_last < total:
        footer = f"\n\n[continua: chame ler_pdf com pagina_inicial={shown_last + 1}]"
    return header + "\n\n" + "\n".join(chunks) + footer


def buscar_no_pdf(caminho: str, termo: str, limite: int = 8) -> str:
    """
    Procura um termo ou conceito dentro de um PDF e devolve as páginas e trechos onde ele aparece.
    Use quando o usuário perguntar sobre um assunto específico de um PDF grande.

    :param caminho: caminho completo do PDF.
    :param termo: palavra ou expressão a procurar (sem diferenciar maiúsculas).
    :param limite: número máximo de trechos (padrão 8).
    """
    path = _check_pdf(caminho)
    query = (termo or "").strip()
    if len(query) < 2:
        return "Informe um termo com pelo menos 2 caracteres."
    total = _pdf_pages(path)
    pages = _extract_pages(path, 1, total) if total else []
    if not any(p.strip() for p in pages):
        return f"'{path.name}' não tem texto pesquisável (PDF escaneado)."

    needle = query.casefold()
    hits: List[str] = []
    for number, text in enumerate(pages, start=1):
        flat = re.sub(r"\s+", " ", text)
        low = flat.casefold()
        start = 0
        while True:
            idx = low.find(needle, start)
            if idx < 0:
                break
            left = max(0, idx - 180)
            right = min(len(flat), idx + len(query) + 180)
            hits.append(f"[página {number}] ...{flat[left:right]}...")
            start = idx + len(needle)
            if len(hits) >= limite:
                break
        if len(hits) >= limite:
            break

    if not hits:
        return f"'{query}' não aparece em '{path.name}' ({total} página(s))."
    return f"'{query}' em '{path.name}': {len(hits)} trecho(s) encontrado(s).\n\n" + "\n\n".join(hits)


def _name_of(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name_of(node.value)
    return None


def _deepest_block(node: ast.AST, depth: int = 0, line: int = 1) -> tuple:
    control = (ast.If, ast.For, ast.While, ast.With, ast.Try)
    best = (depth, line)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, control):
            best = max(best, _deepest_block(child, depth + 1, child.lineno), key=lambda t: t[0])
        else:
            best = max(best, _deepest_block(child, depth, line), key=lambda t: t[0])
    return best


def revisar_codigo_didatico(caminho: str, foco: str = "geral") -> str:
    """
    Faz uma revisão didática de um arquivo Python: procura erros de sintaxe, más práticas comuns
    (except sem tipo, argumentos padrão mutáveis, funções muito longas, aninhamento excessivo,
    imports não usados, eval/exec, variáveis globais) e devolve cada achado com a linha e uma
    explicação. Use antes de dar conselhos sobre o código do usuário. Depois, use read_code_file
    nas linhas indicadas para explicar o trecho.

    :param caminho: caminho completo do arquivo .py.
    :param foco: 'geral', 'seguranca' ou 'legibilidade' (filtra os achados).
    """
    path = _resolve_readable(caminho)
    if not path.is_file():
        return f"Arquivo não encontrado: {path}"
    if path.suffix.lower() != ".py":
        return "A revisão didática suporta apenas arquivos .py neste momento."

    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return (
            f"ERRO DE SINTAXE em {path.name}, linha {exc.lineno}: {exc.msg}.\n"
            f"Explique ao usuário o que o Python espera nessa linha antes de corrigir."
        )

    findings: List[Dict[str, object]] = []

    def add(line: int, categoria: str, mensagem: str, explicacao: str) -> None:
        findings.append({"linha": line, "categoria": categoria, "mensagem": mensagem, "explicacao": explicacao})

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            add(node.lineno, "seguranca", "except sem tipo",
                "Captura todas as exceções, inclusive as de interrupção (Ctrl+C). Prefira capturar o tipo esperado.")
        if isinstance(node, (ast.Call,)) and _name_of(node.func) in {"eval", "exec"}:
            add(node.lineno, "seguranca", f"uso de {_name_of(node.func)}()",
                "Executa texto como código. Se o texto vier de fora, pode executar comandos indesejados.")
        if isinstance(node, ast.Global):
            add(node.lineno, "legibilidade", "variável global",
                "Globais deixam o estado do programa difícil de seguir. Considere passar valores por parâmetro.")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > 50:
                add(node.lineno, "legibilidade", f"função '{node.name}' com {length} linhas",
                    "Funções longas fazem muitas coisas. Separe em funções menores com nomes claros.")
            for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    add(node.lineno, "seguranca", f"padrão mutável em '{node.name}'",
                        "A lista/dicionário padrão é criado uma vez e compartilhado entre chamadas. Use None e crie dentro.")
            if ast.get_docstring(node) is None and not node.name.startswith("_") and length > 8:
                add(node.lineno, "legibilidade", f"função '{node.name}' sem docstring",
                    "Uma docstring curta explica para que serve a função e o que ela recebe e devolve.")

    depth, depth_line = _deepest_block(tree)
    if depth >= 4:
        add(depth_line, "legibilidade", f"aninhamento de blocos até nível {depth}",
            "Muitos if/for/while aninhados são difíceis de ler. Considere retornos antecipados ou funções auxiliares.")

    imported: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    imported[alias.asname or alias.name] = node.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {_name_of(n) for n in ast.walk(tree) if isinstance(n, ast.Attribute)} - {None}
    for name, line in imported.items():
        if name not in used and not re.search(rf"__all__.*{re.escape(name)}", source):
            add(line, "legibilidade", f"import '{name}' não usado",
                "Imports sem uso deixam o arquivo poluído e podem confundir quem lê.")

    if foco in {"seguranca", "legibilidade"}:
        findings = [f for f in findings if f["categoria"] == foco]
    findings.sort(key=lambda f: int(f["linha"]))

    resumo = (
        f"REVISÃO DIDÁTICA: {path.name} | {len(lines)} linhas | "
        f"{sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))} função(ões) | "
        f"{len(findings)} achado(s) (foco: {foco})"
    )
    if not findings:
        return resumo + "\n\nNenhuma má prática detectada pelas regras básicas. Ainda assim, explique os pontos fortes do código."

    out = [resumo, ""]
    for f in findings[:30]:
        out.append(f"- linha {f['linha']} [{f['categoria']}] {f['mensagem']}\n  por quê: {f['explicacao']}")
    if len(findings) > 30:
        out.append(f"... e mais {len(findings) - 30} achado(s).")
    return "\n".join(out)


TUTOR_TOOLS = [
    ler_pdf,
    buscar_no_pdf,
    revisar_codigo_didatico,
]
