"""
Córtex Neural do Obsidian (FASE 2).
Dentro de `OBSIDIAN_VAULT_PATH`, a IA possui autonomia total de leitura e escrita
(sem confirmação [S/N]) para criar/atualizar notas, frontmatter YAML, conexões
`[[wikilinks]]`, diários, agendamentos e gerenciar dinamicamente Skills
(`/01_Skills/`) e Sub-Agentes (`/02_Agentes/`).
"""

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from config import settings


def _resolve_safe_vault_path(relative_path: str) -> Path:
    """
    Garante que qualquer operação do Córtex Neural ocorra estritamente dentro de
    `OBSIDIAN_VAULT_PATH`, prevenindo Path Traversal (`../`).
    Adiciona extensão `.md` automaticamente caso omitida.
    """
    vault_root = settings.OBSIDIAN_VAULT_PATH.expanduser().resolve()
    clean_rel = relative_path.strip().lstrip("/\\")
    if not clean_rel.endswith(".md"):
        clean_rel += ".md"

    target = (vault_root / clean_rel).resolve()
    if not str(target).startswith(str(vault_root)):
        raise PermissionError(
            f"Violação de fronteira do Vault: '{relative_path}' resolve fora de {vault_root}."
        )
    return target


def _format_yaml_frontmatter(
    tags: Optional[List[str]] = None, extra_meta: Optional[Dict[str, str]] = None
) -> str:
    """Constrói um bloco YAML frontmatter limpo e padronizado para o Obsidian."""
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")

    now_iso = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    lines = ["---", f"updated_at: '{now_iso}'"]

    if tags:
        clean_tags = [t.strip().lstrip("#") for t in tags if t.strip()]
        if clean_tags:
            lines.append("tags:")
            for tag in clean_tags:
                lines.append(f"  - {tag}")

    if extra_meta:
        for k, v in extra_meta.items():
            lines.append(f"{k}: '{v}'")

    lines.append("---")
    return "\n".join(lines)


def _extract_wikilinks(markdown_text: str) -> List[str]:
    """Extrai todas as conexões `[[Nota]]` ou `[[Nota|Alias]]` presentes em um texto."""
    matches = re.findall(r"\[\[([^\]]+)\]\]", markdown_text)
    cleaned = []
    for m in matches:
        note_target = m.split("|")[0].split("#")[0].strip()
        if note_target and note_target not in cleaned:
            cleaned.append(note_target)
    return cleaned


# =====================================================================
# TOOLS EXPOSTAS AO AGENTE (AUTONOMIA TOTAL NO VAULT)
# =====================================================================

def read_obsidian_note(relative_path: str) -> str:
    """
    Lê o conteúdo completo de uma nota Markdown dentro do Vault do Obsidian,
    incluindo seu YAML frontmatter e lista de conexões [[wikilinks]].
    Exemplo de relative_path: '00_Persona/personalidade_principal.md' ou 'Projetos/Ideia.md'.
    """
    target = _resolve_safe_vault_path(relative_path)
    if not target.exists():
        return json.dumps(
            {
                "status": "NOT_FOUND",
                "message": f"A nota '{relative_path}' não foi encontrada no Vault.",
            },
            ensure_ascii=False,
        )

    content = target.read_text(encoding="utf-8")
    return json.dumps(
        {
            "status": "OK",
            "relative_path": str(target.relative_to(settings.OBSIDIAN_VAULT_PATH.resolve())),
            "wikilinks_outbound": _extract_wikilinks(content),
            "content": content,
        },
        ensure_ascii=False,
        indent=2,
    )


def write_obsidian_note(
    relative_path: str,
    content: str,
    tags_csv: str = "",
    mode: str = "overwrite",
) -> str:
    """
    Cria ou atualiza autonomamente (sem pedir confirmação) uma nota Markdown no Vault do Obsidian.
    Suporta conexões `[[wikilinks]]` e injeta/atualiza tags YAML frontmatter.
    Parâmetros:
    - relative_path: Caminho relativo dentro do Vault (ex: 'Notas/Arquitetura IA.md').
    - content: Corpo em Markdown (incentivado o uso de [[wikilinks]]).
    - tags_csv: Tags separadas por vírgula (ex: 'ia, arquitetura, pkm').
    - mode: 'overwrite' (substitui a nota) ou 'append' (adiciona ao final da nota existente).
    """
    settings.ensure_directories()
    target = _resolve_safe_vault_path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tags_list = [t.strip() for t in tags_csv.split(",") if t.strip()] if tags_csv else []

    if mode == "append" and target.exists():
        existing = target.read_text(encoding="utf-8")
        final_content = existing.rstrip() + "\n\n" + content.strip() + "\n"
    else:
        # Se o conteúdo já começa com frontmatter YAML, preserva; caso contrário, gera um
        if content.lstrip().startswith("---"):
            final_content = content.strip() + "\n"
        else:
            frontmatter = _format_yaml_frontmatter(tags=tags_list)
            final_content = f"{frontmatter}\n\n{content.strip()}\n"

    target.write_text(final_content, encoding="utf-8")

    return json.dumps(
        {
            "status": "SAVED",
            "path": str(target.relative_to(settings.OBSIDIAN_VAULT_PATH.resolve())),
            "mode": mode,
            "wikilinks_detected": _extract_wikilinks(final_content),
            "bytes_written": len(final_content.encode("utf-8")),
        },
        ensure_ascii=False,
        indent=2,
    )


def append_daily_journal_entry(entry_text: str, section_Or_event: str = "Registro") -> str:
    """
    Adiciona uma anotação, reflexão ou evento agendado na Nota Diária (Daily Note)
    do dia atual em `/03_Diario/YYYY-MM-DD.md` com carimbo de hora exato.
    Autonomia total de escrita sem confirmação.
    """
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    rel_path = f"03_Diario/{date_str}.md"

    target = _resolve_safe_vault_path(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        header = _format_yaml_frontmatter(
            tags=["diario", "daily-note"],
            extra_meta={"date": date_str},
        )
        initial_body = f"{header}\n\n# Diário — {date_str}\n\n"
        target.write_text(initial_body, encoding="utf-8")

    existing = target.read_text(encoding="utf-8")
    formatted_entry = f"### [{time_str}] {section_Or_event}\n{entry_text.strip()}\n"
    updated = existing.rstrip() + "\n\n" + formatted_entry
    target.write_text(updated, encoding="utf-8")

    return json.dumps(
        {
            "status": "JOURNAL_UPDATED",
            "daily_note": rel_path,
            "timestamp": f"{date_str} {time_str}",
            "section": section_Or_event,
        },
        ensure_ascii=False,
        indent=2,
    )


def search_obsidian_vault(query: str) -> str:
    """
    Pesquisa notas dentro do Vault do Obsidian por título, tags YAML,
    conexões `[[wikilinks]]` ou conteúdo textual.
    Retorna os trechos correspondentes e os backlinks encontrados.
    """
    settings.ensure_directories()
    vault_root = settings.OBSIDIAN_VAULT_PATH.resolve()
    q_lower = query.strip().lower()
    results = []

    for md_file in sorted(vault_root.rglob("*.md")):
        rel_str = str(md_file.relative_to(vault_root))
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        if q_lower in rel_str.lower() or q_lower in text.lower():
            # Extrai snippet ao redor do termo
            idx = text.lower().find(q_lower)
            start = max(0, idx - 80) if idx >= 0 else 0
            end = min(len(text), idx + 160) if idx >= 0 else 200
            snippet = text[start:end].replace("\n", " ").strip()

            results.append(
                {
                    "note": rel_str,
                    "wikilinks": _extract_wikilinks(text),
                    "snippet": snippet,
                }
            )

    return json.dumps(
        {
            "query": query,
            "matches_count": len(results),
            "results": results[:20],
        },
        ensure_ascii=False,
        indent=2,
    )


def manage_obsidian_skill(
    action: str,
    skill_name: str,
    description: str = "",
    instructions_markdown: str = "",
) -> str:
    """
    Gerencia dinamicamente Skills no diretório `/01_Skills/` do Obsidian.
    Parâmetros:
    - action: 'list' (lista todas as skills), 'read' (lê uma skill específica) ou 'create_or_update' (cria/atualiza uma skill).
    - skill_name: Nome identificador da skill (ex: 'analise_codigo_python').
    - description: Resumo de quando usar a skill (usado em 'create_or_update').
    - instructions_markdown: Passo a passo e diretrizes da skill em Markdown (usado em 'create_or_update').
    """
    settings.ensure_directories()
    skills_dir = settings.skills_dir_path.resolve()

    if action == "list":
        skills = [f.stem for f in sorted(skills_dir.glob("*.md"))]
        return json.dumps({"action": "list", "skills": skills}, ensure_ascii=False, indent=2)

    safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", skill_name.strip().lower())
    skill_path = _resolve_safe_vault_path(f"01_Skills/{safe_slug}.md")

    if action == "read":
        if not skill_path.exists():
            return json.dumps(
                {"status": "NOT_FOUND", "message": f"Skill '{safe_slug}' não existe em /01_Skills/."},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "OK",
                "skill": safe_slug,
                "content": skill_path.read_text(encoding="utf-8"),
            },
            ensure_ascii=False,
            indent=2,
        )

    if action == "create_or_update":
        frontmatter = _format_yaml_frontmatter(
            tags=["skill", "córtex"],
            extra_meta={"skill_name": safe_slug, "description": description},
        )
        body = (
            f"{frontmatter}\n\n"
            f"# Skill: {skill_name}\n\n"
            f"**Descrição**: {description}\n\n"
            f"## Instruções Operacionais\n{instructions_markdown.strip()}\n"
        )
        skill_path.write_text(body, encoding="utf-8")
        return json.dumps(
            {
                "status": "SKILL_SAVED",
                "path": f"01_Skills/{safe_slug}.md",
                "skill_name": safe_slug,
            },
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(
        {"status": "INVALID_ACTION", "valid_actions": ["list", "read", "create_or_update"]},
        ensure_ascii=False,
    )


def manage_obsidian_subagent(
    action: str,
    agent_name: str,
    role: str = "",
    system_prompt_markdown: str = "",
) -> str:
    """
    Gerencia dinamicamente Sub-Agentes Especialistas no diretório `/02_Agentes/` do Obsidian.
    Parâmetros:
    - action: 'list' (lista sub-agentes), 'read' (carrega definição de um sub-agente) ou 'create_or_update' (cria/atualiza sub-agente).
    - agent_name: Identificador do sub-agente (ex: 'arquiteto_sql', 'revisor_seguranca').
    - role: Papel/Cargo do sub-agente (usado em 'create_or_update').
    - system_prompt_markdown: Prompt de sistema e regras específicas do sub-agente (usado em 'create_or_update').
    """
    settings.ensure_directories()
    agents_dir = settings.subagents_dir_path.resolve()

    if action == "list":
        agents = [f.stem for f in sorted(agents_dir.glob("*.md"))]
        return json.dumps({"action": "list", "subagents": agents}, ensure_ascii=False, indent=2)

    safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_name.strip().lower())
    agent_path = _resolve_safe_vault_path(f"02_Agentes/{safe_slug}.md")

    if action == "read":
        if not agent_path.exists():
            return json.dumps(
                {"status": "NOT_FOUND", "message": f"Sub-Agente '{safe_slug}' não encontrado em /02_Agentes/."},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "status": "OK",
                "subagent": safe_slug,
                "content": agent_path.read_text(encoding="utf-8"),
            },
            ensure_ascii=False,
            indent=2,
        )

    if action == "create_or_update":
        frontmatter = _format_yaml_frontmatter(
            tags=["subagente", "córtex"],
            extra_meta={"agent_name": safe_slug, "role": role},
        )
        body = (
            f"{frontmatter}\n\n"
            f"# Sub-Agente: {agent_name}\n\n"
            f"**Papel (Role)**: {role}\n\n"
            f"## System Prompt Especializado\n{system_prompt_markdown.strip()}\n"
        )
        agent_path.write_text(body, encoding="utf-8")
        return json.dumps(
            {
                "status": "SUBAGENT_SAVED",
                "path": f"02_Agentes/{safe_slug}.md",
                "agent_name": safe_slug,
                "role": role,
            },
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(
        {"status": "INVALID_ACTION", "valid_actions": ["list", "read", "create_or_update"]},
        ensure_ascii=False,
    )


def create_obsidian_json_canvas(
    relative_canvas_path: str,
    nodes_json: str,
    edges_json: str = "[]",
) -> str:
    """
    [Skill: json-canvas / kepano] Cria ou atualiza um arquivo visual `.canvas` (JSON Canvas Spec 1.0)
    dentro do Vault do Obsidian.
    Parâmetros:
    - relative_canvas_path: Caminho relativo no Vault (ex: 'Mapas/Arquitetura_Sophy.canvas').
    - nodes_json: JSON array de nós (cada nó com id, type ['text'|'file'|'link'|'group'], x, y, width, height, text/file/url, color).
    - edges_json: JSON array de conexões (cada aresta com id, fromNode, fromSide, toNode, toSide, label).
    """
    settings.ensure_directories()
    vault_root = settings.OBSIDIAN_VAULT_PATH.expanduser().resolve()
    clean_rel = relative_canvas_path.strip().lstrip("/\\")
    if not clean_rel.endswith(".canvas"):
        if clean_rel.endswith(".md"):
            clean_rel = clean_rel[:-3] + ".canvas"
        else:
            clean_rel += ".canvas"

    target = (vault_root / clean_rel).resolve()
    if not str(target).startswith(str(vault_root)):
        raise PermissionError(f"Caminho fora do Vault: {relative_canvas_path}")
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        nodes = json.loads(nodes_json) if isinstance(nodes_json, str) else nodes_json
    except Exception:
        nodes = [
            {
                "id": "node-1",
                "type": "text",
                "x": 0,
                "y": 0,
                "width": 360,
                "height": 180,
                "text": str(nodes_json),
                "color": "4",
            }
        ]

    try:
        edges = json.loads(edges_json) if isinstance(edges_json, str) else edges_json
    except Exception:
        edges = []

    canvas_payload = {"nodes": nodes, "edges": edges}
    target.write_text(json.dumps(canvas_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return json.dumps(
        {
            "status": "CANVAS_SAVED",
            "path": str(target.relative_to(vault_root)),
            "nodes_count": len(nodes),
            "edges_count": len(edges),
        },
        ensure_ascii=False,
        indent=2,
    )


def create_obsidian_base(
    relative_base_path: str,
    base_yaml_content: str,
) -> str:
    """
    [Skill: obsidian-bases / kepano] Cria ou atualiza uma Base de Dados nativa do Obsidian (`.base` em YAML)
    com filtros (`filters`), fórmulas (`formulas`), propriedades (`properties`) e visualizações (`views`: table, cards, list, map).
    Parâmetros:
    - relative_base_path: Caminho relativo no Vault (ex: 'Bases/Projetos.base').
    - base_yaml_content: Conteúdo YAML válido seguindo a especificação Obsidian Bases.
    """
    settings.ensure_directories()
    vault_root = settings.OBSIDIAN_VAULT_PATH.expanduser().resolve()
    clean_rel = relative_base_path.strip().lstrip("/\\")
    if not clean_rel.endswith(".base"):
        if clean_rel.endswith(".md"):
            clean_rel = clean_rel[:-3] + ".base"
        else:
            clean_rel += ".base"

    target = (vault_root / clean_rel).resolve()
    if not str(target).startswith(str(vault_root)):
        raise PermissionError(f"Caminho fora do Vault: {relative_base_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(base_yaml_content.strip() + "\n", encoding="utf-8")

    return json.dumps(
        {
            "status": "BASE_SAVED",
            "path": str(target.relative_to(vault_root)),
            "bytes_written": len(base_yaml_content.encode("utf-8")),
        },
        ensure_ascii=False,
        indent=2,
    )


def defuddle_web_to_obsidian(
    url: str,
    save_relative_path: str = "",
    tags_csv: str = "clipping,web,defuddle",
) -> str:
    """
    [Skill: defuddle / kepano] Extrai o conteúdo principal limpo em Markdown de uma página web,
    removendo menus, anúncios e ruído HTML, e opcionalmente salva direto como nota no Vault do Obsidian.
    Parâmetros:
    - url: URL completa da página web (http:// ou https://).
    - save_relative_path: Se informado (ex: 'Clippings/Artigo.md'), salva o conteúdo extraído no Vault.
    - tags_csv: Tags YAML para a nota salva.
    """
    if settings.STRICT_OFFLINE_MODE:
        return json.dumps(
            {"status": "BLOCKED_OFFLINE", "message": "Modo Estritamente Offline ativo."},
            ensure_ascii=False,
        )

    import httpx
    from bs4 import BeautifulSoup

    try:
        with httpx.Client(timeout=12.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 SophyDefuddle/2.0"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        return json.dumps({"status": "ERROR", "url": url, "error": str(exc)}, ensure_ascii=False)

    soup = BeautifulSoup(html, "html.parser")
    for noisy in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]):
        noisy.decompose()

    title = (soup.title.string.strip() if soup.title and soup.title.string else url)
    main_node = soup.find("article") or soup.find("main") or soup.body or soup

    lines: List[str] = [f"# {title}", "", f"> Fonte: {url}", ""]
    for elem in main_node.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote"]):
        txt = elem.get_text(" ", strip=True)
        if not txt:
            continue
        if elem.name == "h1":
            lines.append(f"\n# {txt}\n")
        elif elem.name == "h2":
            lines.append(f"\n## {txt}\n")
        elif elem.name == "h3":
            lines.append(f"\n### {txt}\n")
        elif elem.name == "h4":
            lines.append(f"\n#### {txt}\n")
        elif elem.name == "li":
            lines.append(f"- {txt}")
        elif elem.name == "blockquote":
            lines.append(f"> {txt}")
        elif elem.name == "pre":
            lines.append(f"```\n{elem.get_text()}\n```")
        else:
            lines.append(txt + "\n")

    markdown_out = "\n".join(lines).strip()
    saved_note = None
    if save_relative_path.strip():
        res_json = write_obsidian_note(
            relative_path=save_relative_path.strip(),
            content=markdown_out,
            tags_csv=tags_csv,
            mode="overwrite",
        )
        saved_note = json.loads(res_json).get("path")

    return json.dumps(
        {
            "status": "OK",
            "title": title,
            "url": url,
            "saved_to_vault": saved_note,
            "markdown_preview": markdown_out[:3500],
        },
        ensure_ascii=False,
        indent=2,
    )


def render_knap_template(
    template_str: str,
    data_json: str,
    output_vault_note: str = "",
) -> str:
    """
    [Skill: knap / kepano] Renderiza templates declarativos de dados estruturados (`{{campo}}`)
    em lote para Markdown do Obsidian.
    Parâmetros:
    - template_str: Template com placeholders `{{chave}}` (ex: '- **{{nome}}** ({{categoria}}): {{valor}}').
    - data_json: JSON objeto ou array de objetos com os dados.
    - output_vault_note: Se informado, grava o resultado renderizado nessa nota do Vault.
    """
    try:
        parsed = json.loads(data_json) if isinstance(data_json, str) else data_json
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": f"JSON inválido: {exc}"}, ensure_ascii=False)

    items = parsed if isinstance(parsed, list) else [parsed]
    rendered_blocks: List[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        block = template_str
        for k, v in item.items():
            block = block.replace(f"{{{{{k}}}}}", str(v))
        rendered_blocks.append(block)

    final_output = "\n".join(rendered_blocks)
    saved_path = None
    if output_vault_note.strip():
        res = json.loads(write_obsidian_note(output_vault_note.strip(), final_output, tags_csv="knap,template"))
        saved_path = res.get("path")

    return json.dumps(
        {
            "status": "OK",
            "items_rendered": len(rendered_blocks),
            "saved_to_vault": saved_path,
            "rendered_output": final_output,
        },
        ensure_ascii=False,
        indent=2,
    )


def criar_pasta_obsidian(caminho_pasta: str) -> str:
    """
    Cria uma nova pasta (ou subpasta) dentro do vault Obsidian da Sophy, dentro do vault
    (ex: 'Estudos/Python/Aulas'). Use quando o usuário pedir para criar pasta, diretório ou subpasta.

    :param caminho_pasta: caminho relativo da pasta dentro do vault.
    """
    vault_root = settings.OBSIDIAN_VAULT_PATH.expanduser().resolve()
    clean = (caminho_pasta or "").strip().strip("/\\")
    if not clean:
        return json.dumps({"status": "ERRO", "message": "Informe o caminho da pasta."}, ensure_ascii=False)
    target = (vault_root / clean).resolve()
    if not str(target).startswith(str(vault_root) + "/") and target != vault_root:
        raise PermissionError(f"Caminho '{caminho_pasta}' fica fora do vault.")
    if target.exists() and not target.is_dir():
        return json.dumps({"status": "ERRO", "message": f"Já existe um arquivo com o nome '{clean}'."}, ensure_ascii=False)
    already = target.is_dir()
    target.mkdir(parents=True, exist_ok=True)
    return json.dumps(
        {
            "status": "EXISTENTE" if already else "CRIADA",
            "path": str(target.relative_to(vault_root)),
            "absolute_path": str(target),
            "message": "A pasta já existia." if already else "Pasta criada no vault.",
        },
        ensure_ascii=False,
        indent=2,
    )


OBSIDIAN_TOOLS = [
    criar_pasta_obsidian,
    read_obsidian_note,
    write_obsidian_note,
    append_daily_journal_entry,
    search_obsidian_vault,
    manage_obsidian_skill,
    manage_obsidian_subagent,
    create_obsidian_json_canvas,
    create_obsidian_base,
    defuddle_web_to_obsidian,
    render_knap_template,
]
