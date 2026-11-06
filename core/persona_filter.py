"""
Filtro de Personalidade e Percepção Temporal em Tempo Real.
Carrega a identidade da IA a partir de `/00_Persona/personalidade_principal.md`
e injeta metadados temporais exatos antes de cada inferência.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config import settings

DIAS_SEMANA_PT = {
    0: "Segunda-feira",
    1: "Terça-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sábado",
    6: "Domingo",
}

def fill_persona_placeholders(text: str) -> str:
    """Troca {CREATOR_NAME} e {VAULT_PATH} pelos valores do .env (nome do usuário e pasta do vault)."""
    return (
        text.replace("{CREATOR_NAME}", settings.CREATOR_NAME)
        .replace("{VAULT_PATH}", str(settings.OBSIDIAN_VAULT_PATH))
    )


DEFAULT_PERSONA_FALLBACK = fill_persona_placeholders((
    "Você é a Sophy (v2.1), companheira e assistente técnica do {CREATOR_NAME}. "
    "Você tem total permissão para acessar, ler, criar e gerenciar arquivos e notas "
    "no vault do Obsidian localizado no computador do usuário. "
    "Seja direta, prestativa, técnica e natural em suas respostas."
))


class PersonaFilter:
    """Gerencia o carregamento dinâmico da persona no Obsidian e a injeção temporal."""

    def __init__(self, persona_path: Optional[Path] = None, timezone_str: Optional[str] = None):
        self.persona_path: Path = persona_path or settings.persona_file_path
        self.timezone_str: str = timezone_str or settings.TIMEZONE
        self._cached_content: str = DEFAULT_PERSONA_FALLBACK
        self._last_mtime: float = 0.0

    def load_persona(self, force_reload: bool = False) -> str:
        """Lê `/00_Persona/personalidade_principal.md` com cache baseado em mtime."""
        try:
            if not self.persona_path.exists():
                return DEFAULT_PERSONA_FALLBACK

            current_mtime = self.persona_path.stat().st_mtime
            if force_reload or current_mtime > self._last_mtime:
                raw_text = fill_persona_placeholders(self.persona_path.read_text(encoding="utf-8").strip())
                if raw_text:
                    self._cached_content = raw_text
                    self._last_mtime = current_mtime
        except Exception as exc:
            return f"{self._cached_content}\n\n[Aviso interno: falha ao ler persona: {exc}]"

        return self._cached_content

    def get_realtime_temporal_block(self) -> str:
        """Gera o bloco estrito de percepção temporal em tempo real."""
        try:
            tz = ZoneInfo(self.timezone_str)
        except Exception:
            tz = ZoneInfo("UTC")

        now = datetime.now(tz)
        dia_semana = DIAS_SEMANA_PT.get(now.weekday(), "Desconhecido")
        offset_str = now.strftime("%z")
        if len(offset_str) == 5:
            offset_str = f"{offset_str[:3]}:{offset_str[3:]}"

        return (
            "=== PERCEPÇÃO TEMPORAL EM TEMPO REAL ===\n"
            f"- Data Atual: {now.strftime('%Y-%m-%d')} ({now.strftime('%d/%m/%Y')})\n"
            f"- Hora Exata: {now.strftime('%H:%M:%S')}\n"
            f"- Dia da Semana: {dia_semana}\n"
            f"- Fuso Horário: {self.timezone_str} (UTC{offset_str})\n"
            f"- Timestamp ISO-8601: {now.isoformat()}\n"
            "========================================"
        )

    def build_system_prompt(self, mode: str = "creative", extra_instructions: Optional[str] = None) -> str:
        temporal_block = self.get_realtime_temporal_block()
        persona_content = self.load_persona()

        if mode == "tools":
            operational_rules = (
                "=== REGRAS DE ENGENHARIA E EXECUÇÃO (MODO TOOLS) ===\n"
                "1. Forneça argumentos JSON estritos e válidos para ferramentas.\n"
                "2. ZERO ALUCINAÇÃO: Não invente conteúdos de arquivos ou listas; consulte o sistema local diretamente.\n"
                "3. Você tem acesso total de leitura e escrita ao sistema de arquivos permitido e ao Obsidian Vault.\n"
                f"4. Modo Offline Estrito: {'SIM' if settings.STRICT_OFFLINE_MODE else 'NÃO'}.\n"
                "===================================================="
            )
            parts = [
                temporal_block,
                operational_rules,
                "=== IDENTIDADE BASE ===\n",
                persona_content,
            ]
        else:
            conversational_directive = (
                "=== DIRETRIZ GERAL ===\n"
                "- Você é a Sophy (v2.1), assistente pessoal e parceira do {CREATOR_NAME}.\n"
                "- Você possui acesso completo e irrestrito ao sistema local, incluindo pastas, arquivos, código, "
                "hardware e ao vault do Obsidian ({VAULT_PATH}).\n"
                "- Nunca diga que não pode acessar arquivos ou mencionar diretórios do sistema do usuário.\n"
                "- Mantenha uma comunicação clara, autêntica, amigável e resolutiva.\n"
                "======================="
            )
            parts = [
                conversational_directive,
                f"=== IDENTIDADE ATIVA (/00_Persona/personalidade_principal.md) ===\n{persona_content}",
                temporal_block,
            ]

        if extra_instructions:
            parts.append(f"=== CONTEXTO ADICIONAL ===\n{extra_instructions}")

        return "\n\n".join(parts)
