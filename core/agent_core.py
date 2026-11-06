"""
Núcleo do Agente (Orquestrador Local) com suporte assíncrono ao Ollama,
injeção temporal em tempo real, filtro de personalidade modular e loop
estrito de Tool Calling com limite de tentativas (MAX_TOOL_ITERATIONS = 3).
"""

import asyncio
import inspect
import logging
import json
import platform
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Dict, List, Optional

import httpx
import ollama
import psutil

from config import settings
from core.persona_filter import PersonaFilter
from tools.code_tools import CODE_TOOLS
try:  # integração opcional com a Tanya/Sophya (ecossistema em outra pasta)
    from tools.ecosystem_tools import ECOSYSTEM_TOOLS
except Exception:  # pragma: no cover - o ecossistema não é obrigatório
    ECOSYSTEM_TOOLS = []
from tools.engineering_and_catalog_tools import ENGINEERING_AND_CATALOG_TOOLS
from tools.finance_tools import FINANCE_TOOLS
from tools.google_workspace_tools import GOOGLE_WORKSPACE_TOOLS
from tools.hardware_tools import HARDWARE_TOOLS
from tools.media_and_desktop_tools import MEDIA_AND_DESKTOP_TOOLS
from tools.obsidian_tools import OBSIDIAN_TOOLS
from tools.rag_tools import RAG_TOOLS
from tools.sql_tools import SQL_TOOLS, listar_eventos_calendario
from tools.study_tools import STUDY_TOOLS
from tools.diario_tools import DIARIO_TOOLS
from tools.file_tools import FILE_TOOLS, criar_pasta_local
from tools.google_calendar_tools import GOOGLE_CALENDAR_TOOLS, apagar_evento_google, editar_evento_google, listar_eventos_google
from tools.obsidian_tools import criar_pasta_obsidian
from core import diario

logger = logging.getLogger(__name__)
from tools.tutor_tools import TUTOR_TOOLS, buscar_no_pdf, ler_pdf, revisar_codigo_didatico  # noqa: F401
from tools.code_tools import list_allowed_code_files, read_code_file
from tools.whatsapp_tools import WHATSAPP_TOOLS
from workers.sync_manager import SYNC_TOOLS


# Palavras-chave que indicam intenção de acionar ferramentas técnicas/locais
_VAULT_LISTING_RE = re.compile(
    r"\b(list[ea]r?|mostr[ea]r?|ver|veja|quais|conferi[rs]?|estrutura|[áa]rvore)\b"
    r".*\b(pastas?|subpastas?|notas?|vault|cofre|obsidian|diret[óo]rios?)\b"
    r"|\b(pastas?|subpastas?|vault|cofre|obsidian)\b.*\b(list[ea]r?|mostr[ea]r?|quais|estrutura|[áa]rvore)\b",
    re.IGNORECASE,
)
_VAULT_WRITE_RE = re.compile(r"\b(cri[ae]r?|edit[ae]r?|escrev[ae]r?|apag\w*|mov[ae]r?|salv[ae]r?|anex[ae]r?)\b", re.IGNORECASE)


def _is_vault_listing_request(user_message: str) -> bool:
    """Detecta pedidos de listar pastas/notas do vault (sem pedidos de escrita)."""
    if len(user_message) > 160:  # pedidos longos são conversas, não listagens
        return False
    return bool(_VAULT_LISTING_RE.search(user_message)) and not _VAULT_WRITE_RE.search(user_message)


_TUTOR_INTENT_RE = re.compile(
    r"\b(pdf|pdfs|apostila|apostilas|livro|material|relat[óo]rio|tech challenge|"
    r"explique|explica|explicar|explicação|ensin[ea]r?|ensina|d[úu]vidas?|mentor[ae]?|mentoria|"
    r"aprender|estudar|c[óo]digo|codigo|script|scripts|programa[çc][ãa]o|python|sql|"
    r"revis[ae]r?|meu c[óo]digo|meu script|conselho[s]?|o que [ée] isso)\b",
    re.IGNORECASE,
)
_PDF_RE = re.compile(r"\bpdfs?\b", re.IGNORECASE)
_LOCAL_CALENDAR_TOOL_NAMES = {"listar_eventos_calendario", "schedule_local_calendar_event"}
_AGENDA_RE = re.compile(
    r"\b(eventos?|compromissos?|agenda|calend[áa]rio|agendar|marcar|marque|reuni[ãa]o|lembrete)\b",
    re.IGNORECASE,
)
_EDIT_TITLE_RE = re.compile(
    r"\b(mud[ae]r?|altere?|alter[ae]r?|troque?|renomei[ae]r?|edit[ae]r?)\b.*\bevento\s+(.+?)\s+para\s+(?:o\s+t[íi]tulo\s+(?:para\s+)?)?[\"'“‘]?(.+?)[\"'”’]?\s*[.?!]?$",
    re.IGNORECASE,
)
_DELETE_RE = re.compile(r"\b(apag\w*|remov[ae]r?|exclu[ií]r?|delete)\b", re.IGNORECASE)
_CONFIRM_RE = re.compile(r"^\s*(sim|s|confirmo|pode apagar|pode|ok|isso|confirmado|pode sim)\b", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^\s*(n[aã]o|cancel[ae]r?|deixa|esquece)\b", re.IGNORECASE)
_EVENT_LINE_RE = re.compile(r"^- \[([^\]]+)\] (.+?) → (.+?) \| (.+?)(?: \| local:.*)?$")


def _event_name_from(text: str) -> Optional[str]:
    quoted = re.search(r"[\"'“‘]([^\"'”’]{2,80})[\"'”’]", text)
    if quoted:
        return quoted.group(1).strip()
    found = re.search(
        r"\bevento\s+(.+?)(?:\s+(?:do|no|na|da|em|de)\s+(?:meu|minha|nosso|nossa|o|a)?\s*(?:calend[áa]rio|agenda)\b|[?.!]|$)",
        text,
        re.IGNORECASE,
    )
    return found.group(1).strip() if found else None


def _parse_event_lines(texto: str) -> List[Dict[str, str]]:
    events = []
    for line in texto.splitlines():
        m = _EVENT_LINE_RE.match(line.strip())
        if m:
            events.append({"id": m.group(1), "inicio": m.group(2), "fim": m.group(3), "titulo": m.group(4)})
    return events


_FOLDER_CREATE_RE = re.compile(
    r"\b(cri[ae]r?|faz|fa[çc]a|monte|organize)\b.*\bpasta\b|\bnova pasta\b|\bsubpasta\b",
    re.IGNORECASE,
)
_ACTION_CLAIM_RE = re.compile(
    r"\b(criei|criada|criado|criamos|executei|executado|rodei|mkdir|salvei|apaguei|movi|editei|"
    r"atualizei|agendei)\b",
    re.IGNORECASE,
)
_NO_ACTION_WARNING = (
    "\n\n⚠️ Atenção: nenhuma ferramenta foi executada nesta resposta. "
    "Nada foi criado, alterado ou apagado. Se você pediu uma ação, peça de novo que eu tento de verdade."
)
_PY_PATH_RE = re.compile(r"(?:^|(?<=\s))(/[^\s'\"`]+?\.py)\b")
_REVIEW_VERB_RE = re.compile(r"\b(revis[ae]r?|analis[ae]r?|corrig[ae]r?|olh[ae]r?|audit[ae]r?)\b", re.IGNORECASE)
_CODE_REVIEW_RE = re.compile(r"\b(c[óo]digo|script|revis[ae]r?|python|sql|bug|erro)\b", re.IGNORECASE)


_CALENDAR_RE = re.compile(
    r"\b(quais|listar?|liste|mostr[ae]r?|ver|veja|tenho|tem|o que)\b.*\b(eventos?|compromissos?|agenda|calend[áa]rio)\b"
    r"|\b(eventos?|compromissos?|agenda|calend[áa]rio)\b.*\b(marcados?|agendados?|tenho|tem)\b",
    re.IGNORECASE,
)
_CALENDAR_WRITE_RE = re.compile(r"\b(cri[ae]r?|agend[ae]r?|marc[ae]r?|adicion[ae]r?|remov[ae]r?|apag\w*|edit[ae]r?)\b", re.IGNORECASE)


def _is_calendar_listing_request(user_message: str) -> bool:
    if len(user_message) > 160:
        return False
    return bool(_CALENDAR_RE.search(user_message)) and not _CALENDAR_WRITE_RE.search(user_message)


_TOOL_INTENT_KEYWORDS = re.compile(
    r"\b("
    r"obsidian|vault|nota|notas|di[aá]rio|skill|skills|agente|agentes|arquivo|arquivos|pasta|pastas|"
    r"canvas|base|bases|defuddle|knap|template|cat[aá]logo|fun[cç][aã]o|fun[cç][oõ]es|"
    r"c[oó]digo|python|editar|edite|crie|criar|ler|leia|listar|liste|buscar|busque|procurar|procure|"
    r"sql|banco|sqlite|tabela|tabelas|relat[oó]rio|quantos|quantas|contagem|soma|m[eé]dia|gasto|gastos|despesa|"
    r"rag|vetorial|indexar|chromadb|sem[aâ]ntic|"
    r"estudo|estudos|flashcard|flashcards|sm-?2|revis[aã]o|revisar|baralho|"
    r"fan|fans|ventoinha|ventoinhas|cooler|rgb|teclado|cor|brilho|efeito|turbo|silent|performance|temperatura|"
    r"discord|whatsapp|zap|gmail|e-?mail|emails|drive|calendar|agenda|docs|sheets|planilha|sincronizar|sync|worker|workers|"
    r"spotify|m[uú]sica|tocar|play|pause|pausar|pr[oó]xima|faixa|pomodoro|"
    r"finan[cç]a|finan[cç]as|a[cç][aã]o|a[cç][oõ]es|bolsa|ticker|yfinance|dcf|valuation|correla[cç][aã]o|cripto|hyperliquid|bitcoin|btc|"
    r"git|commit|diff|teste|testes|tdd|superpowers|unknowns|ponto cego|pontos cegos|auditoria|"
    r"hardware|vram|ram|gpu|cpu|mem[oó]ria|disco|ssd|status do sistema|"
    r"tanya|sophya|ecossistema|mensagem|mensagens|auditar|auditora|"
    r"pdf|apostila|explique|explica|explicar|ensin\w*|d[úu]vidas?|mentor\w*|aprender|conselhos?|"
    r"eventos?|compromissos?|calend[áa]rio|criar pasta|subpasta"
    r")\b",
    re.IGNORECASE,
)


# =====================================================================
# FERRAMENTAS BASE DE DIAGNÓSTICO (FASE 1 - TESTE DE TOOL CALLING)
# =====================================================================

def list_vault_structure(subpath: str = "", max_depth: int = 3) -> str:
    """
    Lista recursivamente todas as pastas, subpastas e notas (.md) reais dentro
    do Vault do Obsidian (OBSIDIAN_VAULT_PATH).
    Use SEMPRE esta ferramenta quando o usuário pedir para listar notas, pastas,
    subpastas, skills no vault ou ver a estrutura do Obsidian.
    
    Args:
        subpath: Subpasta específica opcional (ex: '01_Skills' ou '01_Skills/Filtros'). Vazio lista o Vault inteiro.
        max_depth: Profundidade máxima da árvore (padrão: 3).
    """
    settings.ensure_directories()
    vault_root = settings.OBSIDIAN_VAULT_PATH.resolve()
    target_dir = (vault_root / subpath.strip("/")).resolve()

    if not str(target_dir).startswith(str(vault_root)) or not target_dir.exists():
        target_dir = vault_root

    tree_lines: List[str] = [f"📦 {vault_root.name} ({vault_root})"]
    total_dirs = 0
    total_notes = 0

    def _walk(current_path: Path, depth: int, prefix: str = "") -> None:
        nonlocal total_dirs, total_notes
        if depth > max_depth:
            return

        # Ignora diretórios/arquivos ocultos (.obsidian, .git, .trash)
        entries = sorted(
            [e for e in current_path.iterdir() if not e.name.startswith(".")],
            key=lambda x: (not x.is_dir(), x.name.lower()),
        )

        dirs = [e for e in entries if e.is_dir()]
        files = [e for e in entries if e.is_file()]

        # Exibe subpastas recursivamente
        for d in dirs:
            total_dirs += 1
            tree_lines.append(f"{prefix}📂 {d.name}/")
            _walk(d, depth + 1, prefix + "  ")

        # Exibe notas/arquivos da pasta atual (limita a 35 por subpasta para não estourar tokens, exceto se subpath for focado)
        limit = 100 if subpath else 35
        for f in files[:limit]:
            total_notes += 1
            display_name = f.stem if f.suffix.lower() == ".md" else f.name
            tree_lines.append(f"{prefix}📄 {display_name}")

        if len(files) > limit:
            total_notes += len(files) - limit
            tree_lines.append(
                f"{prefix}  ... (+{len(files) - limit} notas nesta pasta; passe subpath='{current_path.relative_to(vault_root)}' para ver todas)"
            )

    _walk(target_dir, depth=1, prefix="  ")

    return json.dumps(
        {
            "vault_root": str(vault_root),
            "inspected_path": str(target_dir),
            "total_folders_found": total_dirs,
            "total_files_found": total_notes,
            "vault_tree": "\n".join(tree_lines),
        },
        ensure_ascii=False,
        indent=2,
    )


# =====================================================================
# DIAGNÓSTICO DE HARDWARE E RUNTIME (FASE 1 - TESTE DE TOOL CALLING)
# =====================================================================

def get_hardware_and_runtime_status() -> str:
    """
    Retorna um diagnóstico real do runtime local: CPU, RAM, disco, GPU NVIDIA
    (quando disponível) e se o motor Ollama está respondendo. Use esta ferramenta
    para perguntas sobre VRAM, GPU, RAM, memória, disco ou status do sistema.
    """
    payload: Dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
    }

    try:
        cpu_percent = psutil.cpu_percent(interval=0.2)
        virtual_mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        payload.update(
            {
                "cpu": {
                    "cores_physical": psutil.cpu_count(logical=False),
                    "cores_logical": psutil.cpu_count(logical=True),
                    "usage_percent": cpu_percent,
                },
                "ram": {
                    "total_gb": round(virtual_mem.total / (1024 ** 3), 2),
                    "available_gb": round(virtual_mem.available / (1024 ** 3), 2),
                    "used_percent": virtual_mem.percent,
                },
                "disk_root": {
                    "total_gb": round(disk.total / (1024 ** 3), 2),
                    "free_gb": round(disk.free / (1024 ** 3), 2),
                    "used_percent": disk.percent,
                },
            }
        )
    except Exception as exc:
        payload["system_error"] = f"{type(exc).__name__}: {exc}"

    # GPU NVIDIA (RTX 4050) via nvidia-smi, se presente
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            first_line = result.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) >= 6:
                payload["gpu"] = {
                    "name": parts[0],
                    "vram_total_mb": int(float(parts[1])),
                    "vram_used_mb": int(float(parts[2])),
                    "vram_free_mb": int(float(parts[3])),
                    "utilization_percent": int(float(parts[4])),
                    "temperature_c": int(float(parts[5])),
                }
    except Exception:
        pass

    # Estado do motor Ollama e modelos carregados
    try:
        import httpx as _httpx

        with _httpx.Client(timeout=2.0) as client:
            tags_resp = client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            payload["ollama"] = {
                "reachable": tags_resp.status_code == 200,
                "base_url": settings.OLLAMA_BASE_URL,
                "reasoning_model": settings.OLLAMA_REASONING_MODEL,
                "embed_model": settings.OLLAMA_EMBED_MODEL,
            }
            if tags_resp.status_code == 200:
                models = tags_resp.json().get("models") or []
                payload["ollama"]["installed_models"] = [m.get("name") for m in models if m.get("name")]
    except Exception as exc:
        payload["ollama"] = {
            "reachable": False,
            "base_url": settings.OLLAMA_BASE_URL,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return json.dumps(payload, ensure_ascii=False, indent=2)


# =====================================================================
# ORQUESTRADOR PRINCIPAL DO AGENTE
# =====================================================================

class LocalHybridAgent:
    """
    Orquestrador central da IA Local Híbrida.
    - Injeta data/hora/dia da semana/fuso em tempo real antes de cada inferência.
    - Aplica `temperature=0.1` na fase de decisão de ferramentas/SQL e `0.78` para respostas criativas/persona.
    - Respeita `num_ctx=4096` e `keep_alive="5m"` para proteger os 6GB de VRAM da RTX 4050.
    - Impõe critério de parada estrito (`MAX_TOOL_ITERATIONS = 3`).
    """

    # Fallback Gemini: apenas o modelo configurado no .env (+ aliases ':latest' publicamente conhecidos).
    # Evita 404/cascata de modelos inexistentes (gemini-3.x/3.5) e respeita a escolha do usuário.
    _GEMINI_FALLBACK_MODELS = ("gemini-flash-latest", "gemini-2.5-flash")
    _OPENAI_FALLBACK_MODELS = ("gpt-4o-mini",)

    def __init__(self, tools: Optional[List[Callable[..., Any]]] = None):
        settings.ensure_directories()
        self.client = ollama.AsyncClient(host=settings.OLLAMA_BASE_URL)
        self.persona_filter = PersonaFilter()
        self.history: List[Dict[str, Any]] = []
        self.last_provider_used: str = "ollama_local"
        self._turn_tools: List[Dict[str, Any]] = []
        self._pending_calendar: Optional[Dict[str, str]] = None

        # Registro de ferramentas (mapeia nome -> função executável)
        default_tools: List[Callable[..., Any]] = [
            get_hardware_and_runtime_status,
            list_vault_structure,
            *HARDWARE_TOOLS,
            *OBSIDIAN_TOOLS,
            *CODE_TOOLS,
            *SQL_TOOLS,
            *RAG_TOOLS,
            *STUDY_TOOLS,
            *TUTOR_TOOLS,
            *DIARIO_TOOLS,
            *FILE_TOOLS,
            *GOOGLE_CALENDAR_TOOLS,
            *SYNC_TOOLS,
            *WHATSAPP_TOOLS,
            *GOOGLE_WORKSPACE_TOOLS,
            *FINANCE_TOOLS,
            *ENGINEERING_AND_CATALOG_TOOLS,
            *MEDIA_AND_DESKTOP_TOOLS,
            *ECOSYSTEM_TOOLS,
        ]
        # Agenda única: o Google Calendar. As ferramentas do calendário local ficam fora do registro.
        default_tools = [t for t in default_tools if t.__name__ not in _LOCAL_CALENDAR_TOOL_NAMES]
        active_tools = tools if tools is not None else default_tools
        self.tool_registry: Dict[str, Callable[..., Any]] = {
            func.__name__: func for func in active_tools
        }
        self.tool_ables: List[Callable[..., Any]] = active_tools

    def register_tool(self, func: Callable[..., Any]) -> None:
        """Permite acoplar novas ferramentas dinamicamente (Fases 2 e 3)."""
        self.tool_registry[func.__name__] = func
        if func not in self.tool_ables:
            self.tool_ables.append(func)

    def reset_history(self) -> None:
        """Limpa o histórico de conversação em memória."""
        self.history.clear()

    def _build_ollama_options(self, temperature: float) -> Dict[str, Any]:
        """Retorna os parâmetros estritos de hardware para o Ollama."""
        return {
            "num_ctx": settings.OLLAMA_NUM_CTX,
            "temperature": temperature,
        }

    def _might_need_tools(self, user_message: str) -> bool:
        """Verifica se a mensagem do usuário sugere uso de ferramentas locais."""
        if not self.tool_ables:
            return False
        # Se a pessoa inicializou o agente passando uma lista customizada de tools (ex: testes), sempre avalia tools
        if len(self.tool_ables) < 10:
            return True
        return bool(_TOOL_INTENT_KEYWORDS.search(user_message))

    def _select_tools_for_message(self, user_message: str) -> List[Callable[..., Any]]:
        """
        Filtra as ferramentas enviadas ao LLM conforme o contexto da pergunta,
        evitando estourar a janela de contexto do modelo 7B com 40+ tools simultâneas.
        """
        msg = user_message.lower()

        # Agenda: Google Calendar (criar, editar, apagar, listar)
        if _AGENDA_RE.search(user_message) and not any(kw in user_message.lower() for kw in ("vault", "obsidian", "cofre")):
            agenda_focus = list(GOOGLE_CALENDAR_TOOLS)
            return [t for t in agenda_focus if t.__name__ in self.tool_registry]

        # Criar pasta: ferramenta de arquivos (ou de vault, se o pedido for do cofre)
        if _FOLDER_CREATE_RE.search(user_message):
            focus = [criar_pasta_local, criar_pasta_obsidian]
            return [t for t in focus if t.__name__ in self.tool_registry]

        # Tutoria: PDFs, dúvidas de programação e revisão de código (ferramentas dedicadas, sem vault)
        if _TUTOR_INTENT_RE.search(user_message) and not any(
            kw in msg for kw in ("vault", "obsidian", "cofre")
        ):
            tutor_focus: List[Callable[..., Any]] = [
                ler_pdf,
                buscar_no_pdf,
                revisar_codigo_didatico,
                read_code_file,
                list_allowed_code_files,
            ]
            if not _PDF_RE.search(user_message) and not _CODE_REVIEW_RE.search(user_message):
                tutor_focus = tutor_focus[2:]
            return [t for t in tutor_focus if t.__name__ in self.tool_registry]

        # Se a pergunta fala de vault, obsidian, notas, pastas ou filtros, foca nas tools do Obsidian!
        if any(
            kw in msg
            for kw in (
                "vault",
                "obsidian",
                "nota",
                "notas",
                "pasta",
                "pastas",
                "subpasta",
                "subpastas",
                "filtro",
                "filtros",
                "diário",
                "diario",
                "00_persona",
                "01_skills",
            )
        ):
            focused_tools: List[Callable[..., Any]] = [
                list_vault_structure,
                *OBSIDIAN_TOOLS,
                *RAG_TOOLS,
            ]
            # Mantém apenas funções registradas ativas
            return [t for t in focused_tools if t.__name__ in self.tool_registry]

        return self.tool_ables

    async def _try_cloud_generation(
        self, system_prompt: str, messages_history: List[Dict[str, Any]], temperature: float
    ) -> Optional[str]:
        """
        Tenta alimentar a resposta usando os provedores de nuvem configurados
        (Google AI Studio e OpenAI Code / OpenCode) quando estiver online e fora do
        Modo Estritamente Offline. Faz fallback transparente para o Ollama local se falhar.
        """
        if settings.STRICT_OFFLINE_MODE or not settings.PREFER_CLOUD_WHEN_ONLINE:
            return None

        google_key = settings.GOOGLE_AI_STUDIO_API_KEY.strip()
        openai_code_key = settings.OPENAI_CODE_API_KEY.strip()

        if not google_key and not openai_code_key:
            return None

        # Converte o histórico (incluindo saídas de tools) em mensagens legíveis para síntese
        clean_messages: List[Dict[str, str]] = []
        for m in messages_history:
            role = m.get("role", "user")
            content = m.get("content") or ""
            if role == "tool":
                clean_messages.append(
                    {"role": "user", "content": f"[Resultado de Ferramenta Local]:\n{content}"}
                )
            elif role in ("user", "assistant") and content:
                clean_messages.append({"role": role, "content": str(content)})

        if not clean_messages:
            return None

        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as http_client:
            # 1. Tenta Google AI Studio (Gemini API)
            if google_key:
                candidate_models = [
                    settings.GOOGLE_AI_MODEL,
                    *self._GEMINI_FALLBACK_MODELS,
                ]
                seen_models = []
                for gm in candidate_models:
                    if gm and gm not in seen_models:
                        seen_models.append(gm)

                gemini_contents = []
                for cm in clean_messages:
                    g_role = "user" if cm["role"] == "user" else "model"
                    gemini_contents.append(
                        {"role": g_role, "parts": [{"text": cm["content"]}]}
                    )

                payload_gemini = {
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": gemini_contents,
                    "generationConfig": {
                        "temperature": max(temperature, 0.8),
                        "maxOutputTokens": 1200,
                    },
                }

                for model_name in seen_models:
                    try:
                        url = (
                            f"https://generativelanguage.googleapis.com/v1beta/models/"
                            f"{model_name}:generateContent"
                        )
                        headers = {"Content-Type": "application/json", "x-goog-api-key": google_key}
                        resp = await http_client.post(url, headers=headers, json=payload_gemini)
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates") or []
                            if candidates:
                                parts = (candidates[0].get("content") or {}).get("parts") or []
                                non_thought = [
                                    p.get("text", "") for p in parts if not p.get("thought")
                                ]
                                text_out = "".join(non_thought).strip()
                                if not text_out:
                                    text_out = "".join(p.get("text", "") for p in parts).strip()
                                if text_out:
                                    self.last_provider_used = f"google_ai_studio ({model_name})"
                                    return text_out
                    except (httpx.ConnectError, httpx.TimeoutException):
                        break
                    except Exception:
                        continue

            # 2. Tenta OpenAI Code / OpenCode API (compatível com OpenAI Chat Completions)
            if openai_code_key:
                b_url = settings.OPENAI_CODE_BASE_URL.rstrip("/") or "https://opencode.ai/zen/v1"
                oai_messages = [{"role": "system", "content": system_prompt}, *clean_messages]
                candidate_oai_models = [
                    settings.OPENAI_CODE_MODEL,
                    *self._OPENAI_FALLBACK_MODELS,
                ]
                seen_oai_models = []
                for om in candidate_oai_models:
                    if om and om not in seen_oai_models:
                        seen_oai_models.append(om)

                headers_oai = {
                    "Authorization": f"Bearer {openai_code_key}",
                    "Content-Type": "application/json",
                }

                for o_model in seen_oai_models:
                    try:
                        resp = await http_client.post(
                            f"{b_url}/chat/completions",
                            headers=headers_oai,
                            json={
                                "model": o_model,
                                "messages": oai_messages,
                                "temperature": max(temperature, 0.8),
                                "max_tokens": 1200,
                            },
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            choices = data.get("choices") or []
                            if choices:
                                msg_obj = choices[0].get("message") or {}
                                text_out = (msg_obj.get("content") or "").strip()
                                if text_out:
                                    self.last_provider_used = f"openai_code ({o_model})"
                                    return text_out
                        elif resp.status_code == 429:
                            break
                    except (httpx.ConnectError, httpx.TimeoutException):
                        break
                    except Exception:
                        continue

        return None

    async def _synthesize_creative_reply(self, extra_instructions: Optional[str] = None) -> str:
        """
        Gera a fala final da Sophy (v2.1) em modo 100% criativo e expressivo (sem o peso dos schemas de tools),
        priorizando a alimentação em nuvem (Gemini) quando online e fazendo fallback para o Ollama local.
        """
        creative_prompt = self.persona_filter.build_system_prompt(
            mode="creative", extra_instructions=extra_instructions
        )
        creative_temp = max(settings.OLLAMA_TEMP_CREATIVE, 0.78)

        cloud_reply = await self._try_cloud_generation(
            creative_prompt, self.history, creative_temp
        )
        if cloud_reply:
            self.history.append({"role": "assistant", "content": cloud_reply})
            return cloud_reply

        creative_resp = await self.client.chat(
            model=settings.OLLAMA_REASONING_MODEL,
            messages=[{"role": "system", "content": creative_prompt}, *self._history_for_model()],
            options=self._build_ollama_options(temperature=creative_temp),
            keep_alive=settings.OLLAMA_KEEP_ALIVE,
        )
        try:
            content = (creative_resp.get("message", {}) or {}).get("content") or ""
        except AttributeError:
            content = getattr(getattr(creative_resp, "message", ""), "content", "") or ""
        content = str(content).strip()
        self.last_provider_used = f"ollama_local ({settings.OLLAMA_REASONING_MODEL})"
        self.history.append({"role": "assistant", "content": content})
        return content

    @staticmethod
    def _normalize_assistant_msg(raw: Any) -> Dict[str, Any]:
        """Normaliza a mensagem do Ollama (dict ou Pydantic) preservando tool_calls no histórico."""
        if isinstance(raw, dict):
            content = raw.get("content") or ""
            tool_calls = raw.get("tool_calls")
        else:
            content = getattr(raw, "content", "") or ""
            tool_calls = getattr(raw, "tool_calls", None)

        msg: Dict[str, Any] = {"role": "assistant", "content": str(content or "")}
        if not tool_calls:
            return msg

        # Converte tool calls Pydantic em dicts serializáveis para o histórico do Ollama
        normalized_calls: List[Dict[str, Any]] = []
        for call in tool_calls:
            fn_info = call.get("function", {}) if isinstance(call, dict) else getattr(call, "function", None)
            if isinstance(fn_info, dict):
                fn_name = fn_info.get("name", "") or ""
                fn_args = fn_info.get("arguments", {})
            else:
                fn_name = getattr(fn_info, "name", "") or ""
                fn_args = getattr(fn_info, "arguments", {})

            if isinstance(fn_args, str):
                try:
                    fn_args = json.loads(fn_args)
                except json.JSONDecodeError:
                    fn_args = {}

            normalized_calls.append({"function": {"name": fn_name, "arguments": fn_args or {}}})

        msg["tool_calls"] = normalized_calls
        return msg

    @staticmethod
    def _sanitize_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove turnos 'user' consecutivos (causa de erro de validação de papel na API)."""
        sanitized: List[Dict[str, Any]] = []
        for m in history:
            if m.get("role") == "user" and sanitized and sanitized[-1].get("role") == "user":
                # Junta na mensagem anterior em vez de acumular dois 'user' seguidos.
                sanitized[-1] = {
                    **sanitized[-1],
                    "content": f"{sanitized[-1].get('content', '')}\n\n{m.get('content', '')}",
                }
            else:
                sanitized.append(m)
        return sanitized

    def _history_for_model(self) -> List[Dict[str, Any]]:
        """Histórico saneado e apenas com msgs serializáveis para o Ollama."""
        return self._sanitize_history(self.history)

    async def _execute_tool_safely(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Executa a ferramenta e guarda o resultado para o diário da conversa."""
        result = await self._execute_tool_core(tool_name, tool_args)
        self._turn_tools.append({"ferramenta": tool_name, "args": dict(tool_args or {}), "ok": bool(result.get("ok"))})
        return result

    async def _execute_tool_core(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executa uma ferramenta registrada de forma isolada (síncrona ou assíncrona),
        capturando exceções para alimentar o loop de auto-correção até o limite máximo.
        """
        if tool_name not in self.tool_registry:
            return {
                "ok": False,
                "error": f"Ferramenta '{tool_name}' não existe. Ferramentas disponíveis: {list(self.tool_registry.keys())}",
            }

        target_func = self.tool_registry[tool_name]
        try:
            if inspect.iscoroutinefunction(target_func):
                result = await target_func(**tool_args)
            else:
                result = target_func(**tool_args)
            return {"ok": True, "output": str(result)}
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Erro ao executar '{tool_name}' com args {tool_args}: {type(exc).__name__}: {exc}",
            }

    async def chat(
        self,
        user_message: str,
        on_tool_event: Optional[Callable[[str, Dict[str, Any], Dict[str, Any], int], None]] = None,
    ) -> str:
        """Conversa com a Sophy e registra no diário do dia (comandos, ajuda e alterações)."""
        self._turn_tools = []
        reply = await self._chat_turn(user_message, on_tool_event)
        if not self._turn_tools and _ACTION_CLAIM_RE.search(str(reply)):
            reply = str(reply) + _NO_ACTION_WARNING
        try:
            await asyncio.to_thread(
                diario.registrar_turno, user_message, str(reply), list(self._turn_tools), self.last_provider_used
            )
        except Exception as exc:  # o diário nunca derruba a conversa
            logger.warning(f"Falha ao registrar no diário: {exc}")
        return reply

    async def _chat_turn(
        self,
        user_message: str,
        on_tool_event: Optional[Callable[[str, Dict[str, Any], Dict[str, Any], int], None]] = None,
    ) -> str:
        """
        Processa uma mensagem do usuário executando o roteamento dinâmico de ferramentas,
        o loop estrito de Tool Calling e a síntese final.
        """
        self.history.append({"role": "user", "content": user_message})

        # Apagar evento: procura no Google, mostra e só apaga depois de confirmação explícita.
        pending = getattr(self, "_pending_calendar", None)
        if pending and _CONFIRM_RE.search(user_message):
            self._pending_calendar = None
            resultado = await asyncio.to_thread(apagar_evento_google, pending["id"], True)
            ok = '"status": "APAGADO"' in resultado
            self._turn_tools.append({
                "ferramenta": "apagar_evento_google",
                "args": {"event_id": pending["id"], "titulo": pending["titulo"], "confirmado": True},
                "ok": ok,
            })
            reply = (
                f"Pronto, usuário. Apaguei \"{pending['titulo']}\" do seu Google Calendar."
                if ok else f"Não consegui apagar \"{pending['titulo']}\": {resultado[:300]}"
            )
            self.history.append({"role": "assistant", "content": reply})
            return reply
        if pending and _CANCEL_RE.search(user_message):
            self._pending_calendar = None
            reply = "Tudo bem, cancelei. Nada foi apagado da sua agenda."
            self.history.append({"role": "assistant", "content": reply})
            return reply
        edit_match = _EDIT_TITLE_RE.search(user_message)
        if edit_match and not _DELETE_RE.search(user_message):
            nome = re.sub(r"\s+(do|no|na)\s+(meu|minha|nosso|nossa)?\s*(calend[áa]rio|agenda)$", "", edit_match.group(2).strip(), flags=re.IGNORECASE)
            novo_titulo = edit_match.group(3).strip()
            texto = await asyncio.to_thread(listar_eventos_google, 0, nome)
            achados = _parse_event_lines(texto)
            if not achados:
                reply = f"Não encontrei nenhum evento com \"{nome}\" na sua agenda do Google. Nada foi alterado."
            elif len(achados) > 1:
                linhas = "\n".join(f"- {e['titulo']} ({e['inicio']} → {e['fim']})" for e in achados[:8])
                reply = f"Encontrei {len(achados)} eventos com \"{nome}\". Diga qual deles renomear:\n\n{linhas}\n\nNada foi alterado."
            else:
                e = achados[0]
                resultado = await asyncio.to_thread(editar_evento_google, e["id"], novo_titulo)
                ok = '"status": "EDITADO"' in resultado
                self._turn_tools.append({"ferramenta": "editar_evento_google", "args": {"event_id": e["id"], "titulo": novo_titulo}, "ok": ok})
                reply = (
                    f"Pronto, usuário. Renomeei \"{e['titulo']}\" para \"{novo_titulo}\" no seu Google Calendar. "
                    f"Data e horário continuam: {e['inicio']} → {e['fim']}."
                    if ok else f"Não consegui alterar \"{e['titulo']}\": {resultado[:300]}"
                )
            self.history.append({"role": "assistant", "content": reply})
            return reply
        if _DELETE_RE.search(user_message) and _event_name_from(user_message):
            nome = _event_name_from(user_message) or ""
            texto = await asyncio.to_thread(listar_eventos_google, 0, nome)
            achados = _parse_event_lines(texto)
            if not achados:
                reply = f"Não encontrei nenhum evento com \"{nome}\" na sua agenda do Google. Nada foi apagado."
            elif len(achados) > 1:
                linhas = "\n".join(f"- {e['titulo']} ({e['inicio']} → {e['fim']})" for e in achados[:8])
                reply = (
                    f"Encontrei {len(achados)} eventos com \"{nome}\". Diga qual deles apagar, pelo título e pela data:\n\n{linhas}\n\nNada foi apagado."
                )
            else:
                e = achados[0]
                self._pending_calendar = {"id": e["id"], "titulo": e["titulo"]}
                reply = (
                    f"Encontrei: **{e['titulo']}** ({e['inicio']} → {e['fim']}).\n\n"
                    "Quer que eu apague este evento do seu Google Calendar? Responda **sim** para apagar ou **não** para cancelar."
                )
            self.history.append({"role": "assistant", "content": reply})
            return reply

        # Eventos do calendário: lista direto do banco, sem palpite do modelo.
        if _is_calendar_listing_request(user_message):
            texto = await asyncio.to_thread(listar_eventos_google, 14, "")
            if texto.lstrip().startswith("{"):  # erro do Google: mostra o motivo e cai no calendário local
                texto = "Não consegui ler o Google Calendar agora:\n" + texto + "\n\nCalendário local:\n" + await asyncio.to_thread(listar_eventos_calendario, False, 20)
            reply = f"Estes são os seus próximos eventos, usuário:\n\n```text\n{texto}\n```"
            self.history.append({"role": "assistant", "content": reply})
            return reply

        # Revisão de código: a análise didática local é a fonte; o modelo não reescreve o arquivo.
        review_match = _PY_PATH_RE.search(user_message)
        if review_match and _REVIEW_VERB_RE.search(user_message):
            caminho = review_match.group(1)
            foco = "seguranca" if re.search(r"seguran[çc]a", user_message, re.IGNORECASE) else "geral"
            try:
                analise = await asyncio.to_thread(revisar_codigo_didatico, caminho, foco)
            except PermissionError as exc:
                analise = str(exc)
            reply = (
                f"Revisei `{caminho}` com a análise didática local (sem internet, sem alterar o arquivo):\n\n"
                f"```text\n{analise}\n```\n\n"
                "Escolha um achado pelo número da linha e eu explico com calma, passo a passo. "
                "Antes de ver a solução, me diga o que você acha que ele faz."
            )
            self.history.append({"role": "assistant", "content": reply})
            return reply

        # Listagem do vault é determinística: a árvore real vem do disco, não de um palpite do modelo.
        if _is_vault_listing_request(user_message):
            raw = await asyncio.to_thread(list_vault_structure, "", 4)
            try:
                data = json.loads(raw)
                tree = data.get("vault_tree", raw)
                resumo = f"{data.get('total_folders_found', '?')} pasta(s) e {data.get('total_files_found', '?')} nota(s)"
            except (json.JSONDecodeError, AttributeError):
                tree, resumo = raw, ""
            reply = f"Esta é a estrutura real do seu Vault, usuário ({resumo}):\n\n```text\n{tree}\n```"
            self.history.append({"role": "assistant", "content": reply})
            return reply

        # Se a mensagem é um bate-papo casual que não pede nenhuma ferramenta técnica,
        # responde direto no modo criativo
        if not self._might_need_tools(user_message):
            return await self._synthesize_creative_reply()

        tool_failures = 0
        iterations = 0
        max_iterations = settings.MAX_TOOL_ITERATIONS

        # AQUI ESTÁ A MUDANÇA: Roteamos as ferramentas enviadas para não estourar o contexto
        active_turn_tools = self._select_tools_for_message(user_message)

        while iterations < max_iterations:
            # 1. Atualiza o System Prompt com o relógio em tempo real antes de CADA inferência
            system_prompt = self.persona_filter.build_system_prompt(
                mode="tools" if active_turn_tools else "creative"
            )
            messages_payload = [{"role": "system", "content": system_prompt}, *self._history_for_model()]

            # 2. Inferência com temperature=0.1 para precisão determinística de Tool Calling
            try:
                response = await self.client.chat(
                    model=settings.OLLAMA_REASONING_MODEL,
                    messages=messages_payload,
                    tools=active_turn_tools if active_turn_tools else None,
                    options=self._build_ollama_options(temperature=settings.OLLAMA_TEMP_TOOLS),
                    keep_alive=settings.OLLAMA_KEEP_ALIVE,
                )
                raw_msg = response.get("message", {}) if hasattr(response, "get") else getattr(response, "message", {})
                assistant_msg = self._normalize_assistant_msg(raw_msg)
                tool_calls = assistant_msg.get("tool_calls")
            except Exception as ollama_err:
                # Se o Ollama local estiver desligado mas houver alimentação em nuvem online:
                try:
                    return await self._synthesize_creative_reply()
                except Exception:
                    raise ollama_err

            # Caso o modelo não solicite nenhuma tool, gera a resposta final com a personalidade viva
            if not tool_calls:
                return await self._synthesize_creative_reply()

            # Registra a intenção de chamada de ferramenta no histórico (dict puro, não Pydantic)
            self.history.append(assistant_msg)
            iterations += 1

            # 3. Executa cada tool solicitada pelo LLM
            for call in tool_calls or []:
                try:
                    fn_info = call.get("function", {}) if isinstance(call, dict) else getattr(call, "function", {})
                    if isinstance(fn_info, dict):
                        fn_name = fn_info.get("name", "") or ""
                        raw_args = fn_info.get("arguments", {})
                    else:
                        fn_name = getattr(fn_info, "name", "") or ""
                        raw_args = getattr(fn_info, "arguments", {})
                except Exception:
                    continue

                if isinstance(raw_args, str):
                    try:
                        fn_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        fn_args = {}
                else:
                    fn_args = raw_args or {}

                exec_result = await self._execute_tool_safely(fn_name, fn_args)
                if on_tool_event:
                    on_tool_event(fn_name, fn_args, exec_result, iterations)

                if not exec_result["ok"]:
                    tool_failures += 1
                    tool_output_str = json.dumps(
                        {
                            "status": "ERROR",
                            "attempt": iterations,
                            "max_attempts": max_iterations,
                            "details": exec_result["error"],
                        },
                        ensure_ascii=False,
                    )
                else:
                    tool_output_str = exec_result["output"]

                self.history.append(
                    {
                        "role": "tool",
                        "content": tool_output_str,
                    }
                )

            # Critério de Parada Estrito
            if tool_failures >= max_iterations:
                break

        # 4. Síntese Final pós-Tools com Filtro de Personalidade, modo criativo e BLOQUEIO DE SIMULAÇÃO
        return await self._synthesize_creative_reply(
            extra_instructions=(
                "O ciclo de execução de ferramentas foi concluído. "
                "REGRA ESTRITA: NUNCA simule, invente ou dê exemplos genéricos de pastas ou arquivos. "
                "Apresente EXCLUSIVAMENTE as pastas, subpastas e notas reais retornadas no JSON das ferramentas executadas."
            )
        )
