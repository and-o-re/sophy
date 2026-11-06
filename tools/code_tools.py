"""
Ferramentas de Leitura e Edição Segura de Código (FASE 2).
Fora do Obsidian, apenas caminhos contidos em `ALLOWED_CODE_DIRS` podem ser
acessados. A edição (`apply_code_diff`) opera EXCLUSIVAMENTE por blocos
Search & Replace exatos com criação automática de backup (`.bak.<timestamp>`)
e suporte a confirmação prévia (`require_confirmation`).
"""

from datetime import datetime
import difflib
import json
from pathlib import Path
import shutil
from typing import Callable, Optional

from config import settings

# Callback opcional para confirmação interativa [S/N] no terminal ou GUI
_CONFIRMATION_CALLBACK: Optional[Callable[[str, str], bool]] = None


def set_code_confirmation_callback(callback: Optional[Callable[[str, str], bool]]) -> None:
    """
    Define uma função de callback `fn(file_path, unified_diff) -> bool` para solicitar
    confirmação [S/N] do usuário antes de gravar alterações de código fora do Vault.
    """
    global _CONFIRMATION_CALLBACK
    _CONFIRMATION_CALLBACK = callback


def _resolve_allowed_code_path(file_path: str) -> Path:
    """
    Valida se `file_path` está estritamente dentro de um dos diretórios listados
    em `ALLOWED_CODE_DIRS`. Bloqueia qualquer acesso fora da whitelist.
    """
    candidate = Path(file_path).expanduser().resolve()
    allowed_roots = settings.allowed_code_paths

    for root in allowed_roots:
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue

    allowed_str = ", ".join(str(p) for p in allowed_roots)
    raise PermissionError(
        f"Acesso negado ao caminho '{candidate}'. "
        f"Apenas diretórios em ALLOWED_CODE_DIRS são permitidos: [{allowed_str}]"
    )


# =====================================================================
# TOOLS EXPOSTAS AO AGENTE (PROTEÇÃO ESTRITA EM CÓDIGO)
# =====================================================================

def list_allowed_code_files(directory_path: str, pattern: str = "*.py") -> str:
    """
    Lista arquivos de código dentro de um diretório autorizado em `ALLOWED_CODE_DIRS`.
    Ignora automaticamente pastas `.venv`, `__pycache__`, `.git` e `node_modules`.
    """
    target_dir = _resolve_allowed_code_path(directory_path)
    if not target_dir.exists() or not target_dir.is_dir():
        return json.dumps(
            {"status": "ERROR", "message": f"Diretório '{directory_path}' não existe."},
            ensure_ascii=False,
        )

    ignored_parts = {".venv", "__pycache__", ".git", "node_modules", ".mypy_cache"}
    matched_files = []

    for item in sorted(target_dir.rglob(pattern)):
        if any(part in ignored_parts for part in item.parts):
            continue
        if item.is_file():
            matched_files.append(str(item))

    return json.dumps(
        {
            "status": "OK",
            "directory": str(target_dir),
            "pattern": pattern,
            "count": len(matched_files),
            "files": matched_files[:100],
        },
        ensure_ascii=False,
        indent=2,
    )


def read_code_file(file_path: str, start_line: int = 1, end_line: int = 250) -> str:
    """
    Lê um intervalo de linhas de um arquivo de código dentro de `ALLOWED_CODE_DIRS`
    com numeração de linhas, preservando a janela de contexto (4096 tokens).
    """
    target = _resolve_allowed_code_path(file_path)
    if not target.exists() or not target.is_file():
        return json.dumps(
            {"status": "NOT_FOUND", "message": f"Arquivo '{file_path}' não encontrado."},
            ensure_ascii=False,
        )

    lines = target.read_text(encoding="utf-8").splitlines()
    total_lines = len(lines)
    s_idx = max(1, start_line)
    e_idx = min(total_lines, max(s_idx, end_line))

    numbered_slice = [
        f"{line_no:4d} | {lines[line_no - 1]}" for line_no in range(s_idx, e_idx + 1)
    ]

    return json.dumps(
        {
            "status": "OK",
            "file_path": str(target),
            "total_lines": total_lines,
            "showing_lines": f"{s_idx}-{e_idx}",
            "content": "\n".join(numbered_slice),
        },
        ensure_ascii=False,
        indent=2,
    )


def apply_code_diff(
    file_path: str,
    search_block: str,
    replace_block: str,
    create_backup: bool = True,
) -> str:
    """
    Edita um arquivo de código em `ALLOWED_CODE_DIRS` EXCLUSIVAMENTE via bloco
    Search & Replace exato.
    Regras de Segurança:
    1. Rejeita a operação se `search_block` não existir exatamente no arquivo ou se for ambíguo (múltiplas ocorrências).
    2. Gera um diff unificado para inspeção e, se um callback de confirmação estiver ativo, solicita aprovação [S/N].
    3. Cria automaticamente uma cópia de backup `.bak.<timestamp>` no diretório `.code_backups/` antes de salvar.
    """
    target = _resolve_allowed_code_path(file_path)
    if not target.exists() or not target.is_file():
        return json.dumps(
            {"status": "NOT_FOUND", "message": f"Arquivo '{file_path}' não encontrado."},
            ensure_ascii=False,
        )

    if not search_block:
        return json.dumps(
            {
                "status": "INVALID_SEARCH_BLOCK",
                "message": "O parâmetro 'search_block' não pode ser vazio na edição por Search & Replace.",
            },
            ensure_ascii=False,
        )

    original_text = target.read_text(encoding="utf-8")
    occurrences = original_text.count(search_block)

    if occurrences == 0:
        return json.dumps(
            {
                "status": "SEARCH_BLOCK_NOT_FOUND",
                "message": (
                    "O bloco 'search_block' não foi encontrado exatamente no arquivo. "
                    "Use 'read_code_file' para verificar a indentação e o texto exato antes de tentar novamente."
                ),
            },
            ensure_ascii=False,
        )

    if occurrences > 1:
        return json.dumps(
            {
                "status": "AMBIGUOUS_SEARCH_BLOCK",
                "occurrences": occurrences,
                "message": (
                    f"O bloco 'search_block' aparece {occurrences} vezes no arquivo. "
                    "Inclua mais linhas de contexto adjacentes no 'search_block' para torná-lo único."
                ),
            },
            ensure_ascii=False,
        )

    updated_text = original_text.replace(search_block, replace_block, 1)

    diff_lines = list(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            updated_text.splitlines(keepends=True),
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
            n=3,
        )
    )
    unified_diff_str = "".join(diff_lines)

    # Se houver callback de confirmação interativa registrado, solicita permissão [S/N]
    if _CONFIRMATION_CALLBACK is not None:
        approved = _CONFIRMATION_CALLBACK(str(target), unified_diff_str)
        if not approved:
            return json.dumps(
                {
                    "status": "REJECTED_BY_USER",
                    "file_path": str(target),
                    "message": "O usuário recusou a aplicação do diff neste arquivo de código.",
                },
                ensure_ascii=False,
            )

    backup_path_str: Optional[str] = None
    if create_backup:
        backup_dir = target.parent / ".code_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"{target.name}.bak.{ts}"
        shutil.copy2(target, backup_file)
        backup_path_str = str(backup_file)

    target.write_text(updated_text, encoding="utf-8")

    return json.dumps(
        {
            "status": "APPLIED",
            "file_path": str(target),
            "backup_created": backup_path_str,
            "diff_applied": unified_diff_str,
        },
        ensure_ascii=False,
        indent=2,
    )


CODE_TOOLS = [
    list_allowed_code_files,
    read_code_file,
    apply_code_diff,
]
