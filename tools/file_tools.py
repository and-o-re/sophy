"""Ferramentas de arquivos locais da Sophy (criar pastas fora do vault).

Só criam pastas dentro das raízes graváveis: ALLOWED_CODE_DIRS, ~/Documents e ~/Downloads.
Não apagam nem sobrescrevem nada.
"""

import json
from pathlib import Path
from typing import List

from config import settings

EXTRA_WRITABLE_ROOTS = ["~/Documents", "~/Downloads"]


def _writable_roots() -> List[Path]:
    roots = list(settings.allowed_code_paths)
    roots += [Path(p).expanduser().resolve() for p in EXTRA_WRITABLE_ROOTS]
    return list(dict.fromkeys(roots))


def criar_pasta_local(caminho: str) -> str:
    """
    Cria uma pasta (ou subpastas) no computador do usuário, em Documents, Downloads ou nos projetos
    permitidos. Use quando ele pedir para criar pasta fora do vault do Obsidian.
    Não apaga nem move nada. Responda com o caminho que a ferramenta devolver.

    :param caminho: caminho completo da pasta, ex: ~/Documents/Minha_Pasta.
    """
    raw = (caminho or "").strip()
    if not raw:
        return json.dumps({"status": "ERRO", "message": "Informe o caminho completo da pasta."}, ensure_ascii=False)
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.home() / "Documents" / raw
    target = target.resolve()

    for root in _writable_roots():
        try:
            target.relative_to(root)
            break
        except ValueError:
            continue
    else:
        allowed = ", ".join(str(r) for r in _writable_roots())
        return json.dumps(
            {"status": "NEGADO", "message": f"Não posso criar pastas em '{target}'. Permitido apenas em: {allowed}"},
            ensure_ascii=False,
        )

    if target.exists() and not target.is_dir():
        return json.dumps({"status": "ERRO", "message": f"Já existe um arquivo com este nome: {target}"}, ensure_ascii=False)
    existed = target.is_dir()
    target.mkdir(parents=True, exist_ok=True)
    return json.dumps(
        {
            "status": "EXISTENTE" if existed else "CRIADA",
            "absolute_path": str(target),
            "message": "A pasta já existia." if existed else "Pasta criada com sucesso.",
        },
        ensure_ascii=False,
        indent=2,
    )


FILE_TOOLS = [criar_pasta_local]
