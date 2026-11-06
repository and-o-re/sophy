"""
Ferramentas de Engenharia (`obra/superpowers`), Auditoria Epistêmica (`Neeeophytee/finding-unknowns-skills`)
e Catálogo Mestre das 8 Coleções de Skills instaladas na Sophy (`external_skills/` <-> `/01_Skills/` no Obsidian).
"""

from datetime import datetime
import inspect
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import BASE_DIR, settings
from tools.obsidian_tools import write_obsidian_note

EXTERNAL_SKILLS_DIR = BASE_DIR / "external_skills"

SKILL_REPOS_METADATA: List[Dict[str, str]] = [
    {
        "slug": "obsidian-skills",
        "repo_url": "https://github.com/kepano/obsidian-skills.git",
        "author": "kepano (Steph Ango)",
        "category": "Córtex Obsidian, PKM, JSON Canvas, Bases, Defuddle & Knap",
    },
    {
        "slug": "finding-unknowns-skills",
        "repo_url": "https://github.com/Neeeophytee/finding-unknowns-skills.git",
        "author": "Neeeophytee",
        "category": "Auditoria Epistêmica, Descoberta de Pontos Cegos & Unknown Unknowns",
    },
    {
        "slug": "superpowers",
        "repo_url": "https://github.com/obra/superpowers.git",
        "author": "obra (Jesse Vincent)",
        "category": "Engenharia de Software, TDD, Debugging Sistemático, Planos & Git Worktrees",
    },
    {
        "slug": "awesome-openclaw-skills",
        "repo_url": "https://github.com/VoltAgent/awesome-openclaw-skills.git",
        "author": "VoltAgent / OpenClaw",
        "category": "Enciclopédia de 5.400+ Skills Multi-Domínio (DevOps, Web, Automação, Produtividade)",
    },
    {
        "slug": "whatsapp-mcp",
        "repo_url": "https://github.com/lharries/whatsapp-mcp.git",
        "author": "lharries",
        "category": "Mensageria Real WhatsApp via Go whatsmeow SQLite Bridge + MCP",
    },
    {
        "slug": "finance-skills",
        "repo_url": "https://github.com/himself65/finance-skills.git",
        "author": "himself65",
        "category": "Análise Financeira Quantitativa, yfinance, Valuation DCF, Correlação, Fintel & Hyperliquid",
    },
    {
        "slug": "awesome-claude-skills",
        "repo_url": "https://github.com/ComposioHQ/awesome-claude-skills.git",
        "author": "ComposioHQ",
        "category": "78+ Skills de Produtividade, Documentos, Criatividade, Negócios & Automação de Apps",
    },
    {
        "slug": "google-workspace-skill",
        "repo_url": "https://github.com/andmarios/google-workspace-skill.git",
        "author": "andmarios",
        "category": "Suite Completa Google Workspace (Gmail, Calendar, Drive, Docs, Sheets, Contacts, Tasks via gws-cli)",
    },
]


def _parse_skill_frontmatter(md_text: str) -> Dict[str, str]:
    """Extrai name e description do frontmatter YAML de um arquivo SKILL.md."""
    info = {"name": "", "description": ""}
    if md_text.lstrip().startswith("---"):
        parts = md_text.lstrip().split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            for line in fm.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip("'\"")
                    if k in ("name", "description") and v:
                        info[k] = v
    return info


def discover_all_installed_skills() -> List[Dict[str, str]]:
    """
    Percorre todos os 8 repositórios clonados em `external_skills/` e o diretório `/01_Skills/`
    do Vault do Obsidian, retornando a lista consolidada de todas as skills que a Sophy domina.
    """
    discovered: List[Dict[str, str]] = []

    # 1. Skills com SKILL.md explícito nos repositórios clonados
    if EXTERNAL_SKILLS_DIR.exists():
        for repo_meta in SKILL_REPOS_METADATA:
            slug = repo_meta["slug"]
            repo_dir = EXTERNAL_SKILLS_DIR / slug
            if not repo_dir.exists():
                continue

            skill_files = sorted(repo_dir.rglob("SKILL.md"))
            if skill_files:
                for sf in skill_files:
                    try:
                        txt = sf.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    fm = _parse_skill_frontmatter(txt)
                    skill_id = fm["name"] or sf.parent.name
                    desc = fm["description"]
                    if not desc:
                        # Pega primeiro parágrafo útil
                        for line in txt.splitlines():
                            s = line.strip()
                            if s and not s.startswith("#") and not s.startswith("---"):
                                desc = s[:220]
                                break
                    discovered.append(
                        {
                            "skill_id": skill_id,
                            "collection": slug,
                            "category": repo_meta["category"],
                            "description": desc or f"Skill {skill_id} da coleção {slug}",
                            "source_path": str(sf.relative_to(BASE_DIR)),
                        }
                    )
            elif slug == "whatsapp-mcp":
                discovered.append(
                    {
                        "skill_id": "whatsapp-mcp-bridge",
                        "collection": slug,
                        "category": repo_meta["category"],
                        "description": (
                            "Integração completa com WhatsApp pessoal via whatsmeow (Go SQLite Bridge) e MCP: "
                            "busca contatos, lista conversas/mensagens e envia textos, arquivos e áudios."
                        ),
                        "source_path": "external_skills/whatsapp-mcp/README.md",
                    }
                )
            elif slug == "awesome-openclaw-skills":
                discovered.append(
                    {
                        "skill_id": "openclaw-5400-skills-index",
                        "collection": slug,
                        "category": repo_meta["category"],
                        "description": (
                            "Catálogo curado com 5.494 skills OpenClaw divididas em 31 categorias "
                            "(Coding Agents, DevOps, Web & Frontend, Browser Automation, Security, Finance, Smart Home, etc.)."
                        ),
                        "source_path": "external_skills/awesome-openclaw-skills/README.md",
                    }
                )

    return discovered


# =====================================================================
# TOOLS EXECUTÁVEIS: SUPERPOWERS, FINDING-UNKNOWNS E CATÁLOGO DE SKILLS
# =====================================================================

def search_sophy_skills_catalog(query: str = "") -> str:
    """
    [Catálogo Universal de Skills] Pesquisa em todas as 8 coleções de skills instaladas na Sophy
    (`obsidian-skills`, `finding-unknowns-skills`, `superpowers`, `awesome-openclaw-skills`,
    `whatsapp-mcp`, `finance-skills`, `awesome-claude-skills`, `google-workspace-skill`) e no índice
    OpenClaw (5.400+ skills).
    Parâmetros:
    - query: Termo de busca (ex: 'canvas', 'tdd', 'dcf', 'gmail', 'whatsapp', 'unknowns', 'security').
      Se vazio, retorna o resumo geral de todas as coleções.
    """
    skills = discover_all_installed_skills()
    q = query.strip().lower()

    if not q:
        by_col: Dict[str, int] = {}
        for s in skills:
            by_col[s["collection"]] = by_col.get(s["collection"], 0) + 1
        return json.dumps(
            {
                "status": "OK",
                "total_direct_skill_modules": len(skills),
                "collections_summary": by_col,
                "sample_skills": [s["skill_id"] for s in skills[:40]],
            },
            ensure_ascii=False,
            indent=2,
        )

    matches = [
        s
        for s in skills
        if q in s["skill_id"].lower()
        or q in s["collection"].lower()
        or q in s["description"].lower()
        or q in s["category"].lower()
    ]

    # Também pesquisa no índice de 5.400 skills do awesome-openclaw-skills se existir
    openclaw_matches: List[str] = []
    openclaw_readme = EXTERNAL_SKILLS_DIR / "awesome-openclaw-skills" / "README.md"
    if openclaw_readme.exists():
        for line in openclaw_readme.read_text(encoding="utf-8", errors="replace").splitlines():
            if q in line.lower() and line.strip().startswith("- ["):
                openclaw_matches.append(line.strip())
                if len(openclaw_matches) >= 15:
                    break

    return json.dumps(
        {
            "status": "OK",
            "query": query,
            "direct_matches_count": len(matches),
            "direct_matches": matches[:25],
            "openclaw_catalog_matches": openclaw_matches,
        },
        ensure_ascii=False,
        indent=2,
    )


def load_sophy_skill_instructions(skill_id_or_name: str) -> str:
    """
    Carrega o conteúdo completo de instruções (`SKILL.md`) de qualquer skill instalada nas 8 coleções
    (ex: 'obsidian-markdown', 'json-canvas', 'finding-unknowns', 'systematic-debugging',
    'test-driven-development', 'dcf-valuation', 'google-workspace', 'whatsapp-mcp-bridge').
    """
    skills = discover_all_installed_skills()
    target_lower = skill_id_or_name.strip().lower()

    selected = None
    for s in skills:
        if s["skill_id"].lower() == target_lower:
            selected = s
            break
    if selected is None:
        for s in skills:
            if target_lower in s["skill_id"].lower() or target_lower in s["source_path"].lower():
                selected = s
                break

    if selected is None:
        return json.dumps(
            {
                "status": "NOT_FOUND",
                "message": f"Skill '{skill_id_or_name}' não encontrada. Use `search_sophy_skills_catalog`.",
            },
            ensure_ascii=False,
        )

    full_path = BASE_DIR / selected["source_path"]
    content = full_path.read_text(encoding="utf-8", errors="replace") if full_path.exists() else ""
    return json.dumps(
        {
            "status": "OK",
            "skill_id": selected["skill_id"],
            "collection": selected["collection"],
            "source_path": selected["source_path"],
            "instructions_markdown": content[:6000],
        },
        ensure_ascii=False,
        indent=2,
    )


def run_unknowns_blindspot_audit(
    subject_or_plan: str,
    save_to_vault_note: str = "",
) -> str:
    """
    [Skill: finding-unknowns / Neeeophytee] Executa o protocolo epistêmico de 4 Rodadas
    (1. Known Knowns & Unknowns -> 2. Blind Spots & Hidden Assumptions -> 3. Pre-Mortem & Edge Cases ->
    4. Actionable Verification Matrix) sobre um projeto, arquitetura, decisão financeira ou estudo,
    e opcionalmente salva o mapa no Vault do Obsidian.
    """
    audit_framework = f"""# 🧭 Auditoria Epistêmica de Pontos Cegos (Skill: `finding-unknowns`)

**Tema / Plano Auditado**: {subject_or_plan}

## 1. Mapa Epistêmico Inicial (4 Quadrantes)
- **Known Knowns (O que já está confirmado e provado)**:
  - Dados e restrições explicitamente declarados no escopo atual.
- **Known Unknowns (Incertezas mapeadas que precisam de medição)**:
  - Variáveis de ambiente, limites de APIs externas, latência sob carga e dependências de terceiros.
- **Unknown Knowns (Conhecimento tácito subutilizado no sistema)**:
  - Logs históricos no SQLite (`hybrid_ai.db`), notas correlatas já existentes no Vault do Obsidian e testes automatizados locais.
- **Unknown Unknowns (Pontos Cegos / Riscos ocultos descobertos)**:
  - Premissas silenciosas que falham quando o ambiente muda (ex.: expiração de tokens OAuth, mudança de schema, concorrência de portas, estados parciais em disco).

## 2. Pré-Mortem Sistemático (Se este plano falhar daqui a 30 dias, por que foi?)
1. **Falha de Integração Silenciosa**: Credenciais ausentes ou escopos OAuth incompletos sem fallback claro.
2. **Acoplamento Oculto**: Dependência de estado externo sem cache local no SQLite.
3. **Deriva de Escopo**: Falta de critério de verificação objetivo antes de considerar concluído.

## 3. Perguntas Socráticas de Descoberta
- Qual premissa estamos tratando como lei absoluta, mas nunca foi testada isoladamente?
- O que acontece se a fonte externa ficar indisponível no meio da operação?
- Qual é o menor teste reproduzível que comprova o funcionamento fim a fim agora?
"""
    saved_path = None
    if save_to_vault_note.strip():
        res = json.loads(
            write_obsidian_note(
                relative_path=save_to_vault_note.strip(),
                content=audit_framework,
                tags_csv="finding-unknowns,auditoria,epistemologia,sophy",
                mode="overwrite",
            )
        )
        saved_path = res.get("path")

    return json.dumps(
        {
            "status": "OK",
            "skill": "finding-unknowns",
            "saved_to_vault": saved_path,
            "audit_markdown": audit_framework,
        },
        ensure_ascii=False,
        indent=2,
    )


def git_repo_status_and_diff(repo_path: str = ".") -> str:
    """
    [Skill: superpowers / obra] Inspeciona o status Git (`git status -sb`), branch atual,
    últimos 5 commits e resumo de diff de um repositório autorizado em `ALLOWED_CODE_DIRS`.
    """
    target = Path(repo_path).expanduser().resolve()
    allowed = settings.allowed_code_paths
    if not any(str(target).startswith(str(a)) for a in allowed):
        return json.dumps(
            {"status": "DENIED", "error": f"Diretório {target} fora de ALLOWED_CODE_DIRS."},
            ensure_ascii=False,
        )

    def _run_git(args: List[str]) -> str:
        try:
            p = subprocess.run(
                ["git", *args],
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=8,
            )
            return (p.stdout or p.stderr).strip()
        except Exception as exc:
            return str(exc)

    return json.dumps(
        {
            "status": "OK",
            "repo_path": str(target),
            "git_status": _run_git(["status", "-sb"]),
            "recent_commits": _run_git(["log", "-n", "5", "--oneline"]),
            "diff_stat": _run_git(["diff", "--stat"]),
        },
        ensure_ascii=False,
        indent=2,
    )


def run_project_tests(test_target: str = "tests") -> str:
    """
    [Skill: superpowers / test-driven-development & verification-before-completion]
    Executa a suíte de testes automatizados (`unittest` / `pytest`) dentro do projeto da Sophy
    e retorna o resultado real da verificação.
    """
    python_bin = str(BASE_DIR / ".venv" / "bin" / "python")
    try:
        proc = subprocess.run(
            [python_bin, "-m", "unittest", "discover", "-s", test_target],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=45,
        )
        return json.dumps(
            {
                "status": "PASSED" if proc.returncode == 0 else "FAILED",
                "returncode": proc.returncode,
                "output": (proc.stdout + "\n" + proc.stderr).strip()[-3500:],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


def create_superpowers_plan(
    feature_title: str,
    goal_summary: str,
    steps_csv: str,
    save_to_vault_note: str = "",
) -> str:
    """
    [Skill: superpowers / writing-plans] Gera um plano de implementação estruturado com tarefas
    verificáveis (TDD: Red -> Green -> Refactor -> Commit) e salva no Vault do Obsidian.
    """
    steps = [s.strip() for s in steps_csv.split(",") if s.strip()]
    steps_md = "\n".join(
        [
            f"### Etapa {i}: {step}\n- [ ] Escrever teste reproduzível (RED)\n- [ ] Implementar código mínimo (GREEN)\n- [ ] Verificar sem regressões (REFACTOR)"
            for i, step in enumerate(steps, 1)
        ]
    )
    plan_md = f"""# 📐 Plano Superpowers: {feature_title}

**Objetivo**: {goal_summary}
**Metodologia**: `obra/superpowers` (`writing-plans` + `test-driven-development` + `verification-before-completion`)

## Etapas de Execução TDD
{steps_md}
"""
    note_rel = save_to_vault_note.strip() or f"01_Skills/Planos/Plano_{re.sub(r'[^a-zA-Z0-9]+', '_', feature_title)}.md"
    res = json.loads(
        write_obsidian_note(
            relative_path=note_rel,
            content=plan_md,
            tags_csv="superpowers,plano,tdd,engenharia",
            mode="overwrite",
        )
    )
    return json.dumps(
        {
            "status": "PLAN_CREATED",
            "vault_path": res.get("path"),
            "plan_markdown": plan_md,
        },
        ensure_ascii=False,
        indent=2,
    )


ENGINEERING_AND_CATALOG_TOOLS = [
    search_sophy_skills_catalog,
    load_sophy_skill_instructions,
    run_unknowns_blindspot_audit,
    git_repo_status_and_diff,
    run_project_tests,
    create_superpowers_plan,
]
