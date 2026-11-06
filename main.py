"""
Ponto de entrada CLI da Sophy — Sophy v2.1 (IA Local Híbrida & Maga do Santuário):
- Sobe e abre automaticamente o Painel Desktop (127.0.0.1:8080) junto com o terminal
- Estilizado com o mesmo tema do Painel Desktop: Santuário das Ruínas Antigas & Floresta de Musgo (16-Bit Pixel Fantasy)
- Exibe um único Painel de Status conciso mostrando que a Sophy (v2.1) está ONLINE,
  quais conexões estão operando (WhatsApp, Discord, Grimório Obsidian, Hardware) e quais
  chaves de API estão em uso
- Inicia o chat interativo no terminal com a personalidade de maga fofa da Sophy (irmã mais nova da Sophya)
"""

import os
import sys

# --offline precisa valer antes de carregar a configuração (config lê o ambiente na importação).
if "--offline" in sys.argv:
    os.environ["STRICT_OFFLINE_MODE"] = "true"
    sys.argv.remove("--offline")

import argparse
import asyncio
from datetime import datetime
import json
import subprocess
import sys
from typing import Any, Dict
from zoneinfo import ZoneInfo

import httpx
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.theme import Theme

from config import BASE_DIR, settings
from core.agent_core import LocalHybridAgent
from gui.desktop_launcher import open_lightweight_desktop_window
from tools.code_tools import set_code_confirmation_callback
from tools.hardware_tools import get_fans_and_rgb_status
from workers.discord_bot import get_discord_live_status, sophy_discord_service
from workers.sync_manager import _read_env_credentials

# ==============================================================================
# TEMA DO TERMINAL IGUAL AO PAINEL DESKTOP (SANTUÁRIO DAS RUÍNAS ANTIGAS 16-BIT)
# Paleta extraída diretamente de gui/frontend/styles.css
# ==============================================================================
RUINS_PANEL_THEME = Theme(
    {
        "ruins.gold": "bold #d8b86a",
        "ruins.amber": "bold #cfa04e",
        "ruins.sage": "bold #86cf90",
        "ruins.leaf": "#62ab6c",
        "ruins.moss": "bold #4d8a56",
        "ruins.emerald": "#2f5736",
        "ruins.text": "#e4f0df",
        "ruins.muted": "#8fa990",
        "ruins.ruby": "bold #b84a4a",
        "markdown.h1": "bold #d8b86a",
        "markdown.h2": "bold #86cf90",
        "markdown.h3": "bold #cfa04e",
        "markdown.code": "bold #86cf90 on #132017",
        "markdown.link": "underline #86cf90",
    }
)

console = Console(theme=RUINS_PANEL_THEME)


def _mask_key(val: str) -> str:
    """Mascara chaves de API para exibição segura no painel inicial do terminal."""
    val = (val or "").strip()
    if not val:
        return "Não configurada"
    if len(val) <= 10:
        return f"{val[:3]}***"
    return f"{val[:6]}...{val[-4:]}"


def ask_user_code_diff_confirmation(file_path: str, unified_diff: str) -> bool:
    """Exibe o diff unificado no terminal com tema das Ruínas e solicita confirmação [S/N] antes de alterar código."""
    console.print(
        Panel(
            Syntax(unified_diff, "diff", theme="monokai", line_numbers=False),
            title=f"[ruins.ruby]❖ Pergaminho de Restauração de Código (Search & Replace): {file_path}[/ruins.ruby]",
            border_style="#b84a4a",
            box=box.SQUARE,
        )
    )
    ans = (
        console.input(
            "[ruins.gold]🪄 Deseja selar este feitiço de código e criar backup (.bak)? [S/N]: [/ruins.gold]"
        )
        .strip()
        .lower()
    )
    return ans in {"s", "sim", "y", "yes"}


def log_tool_execution(
    tool_name: str, tool_args: Dict[str, Any], result: Dict[str, Any], iteration: int
) -> None:
    status_style = "ruins.sage" if result.get("ok") else "ruins.ruby"
    status_label = "FEITIÇO CONCLUÍDO" if result.get("ok") else "FALHA NA RUNA"
    console.print(
        Panel(
            f"[ruins.gold]❖ Runa / Tool:[/ruins.gold] [ruins.text]{tool_name}[/ruins.text]\n"
            f"[ruins.gold]❖ Argumentos:[/ruins.gold] [ruins.muted]{tool_args}[/ruins.muted]\n"
            f"[ruins.gold]❖ Ciclo Arcano:[/ruins.gold] [ruins.text]{iteration}/{settings.MAX_TOOL_ITERATIONS}[/ruins.text]\n"
            f"[{status_style}]❖ Estado: {status_label}[/{status_style}]",
            title="[ruins.gold]🪄 Invocação de Grimório (Tool Calling)[/ruins.gold]",
            border_style="#4d8a56",
            box=box.SQUARE,
        )
    )


async def ensure_panel_running_and_open(open_window: bool = True) -> bool:
    """
    Garante que o backend do Painel da Sophy (127.0.0.1:8080) esteja ativo
    e abre a janela gráfica do Painel junto com o terminal.
    """
    url = f"http://{settings.GUI_HOST}:{settings.GUI_PORT}"
    panel_online = False

    async with httpx.AsyncClient(timeout=1.5) as client:
        try:
            resp = await client.get(f"{url}/api/telemetry")
            if resp.status_code == 200:
                panel_online = True
        except Exception:
            panel_online = False

        if not panel_online:
            try:
                log_file = open("/tmp/hybrid_ai_gui.log", "a", encoding="utf-8")
                subprocess.Popen(
                    [sys.executable, "-m", "gui.desktop_launcher", "--headless"],
                    cwd=str(BASE_DIR),
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True,
                )
                for _ in range(10):
                    await asyncio.sleep(0.25)
                    try:
                        resp = await client.get(f"{url}/api/telemetry")
                        if resp.status_code == 200:
                            panel_online = True
                            break
                    except Exception:
                        continue
            except Exception:
                panel_online = False

    if open_window:
        try:
            open_lightweight_desktop_window(url, delay=0.1)
        except Exception:
            pass

    return panel_online


async def render_sophy_startup_panel(panel_online: bool = True) -> None:
    """
    Exibe o altar/painel de inicialização da Sophy (v2.1) no mesmo tema
    visual do Painel Desktop (Santuário das Ruínas Antigas • Pedra, Musgo, Sálvia e Ouro Antigo).
    """
    creds = _read_env_credentials()
    try:
        tz = ZoneInfo(settings.TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d/%m/%Y %H:%M:%S")

    # 1. Verifica Motor Local Ollama
    ollama_ok = False
    async with httpx.AsyncClient(timeout=1.8) as local_client:
        try:
            r_ollama = await local_client.get(f"{settings.OLLAMA_BASE_URL}/api/tags")
            ollama_ok = r_ollama.status_code == 200
        except Exception:
            ollama_ok = False

        # 2. Verifica Bridge Local do WhatsApp
        wpp_port = creds.get("WHATSAPP_BRIDGE_PORT", str(settings.WHATSAPP_BRIDGE_PORT)).strip() or "3001"
        wpp_paired = creds.get("WHATSAPP_PAIRED", str(settings.WHATSAPP_PAIRED)).lower() == "true"
        wpp_bridge_live = False
        try:
            r_wpp = await local_client.get(f"http://127.0.0.1:{wpp_port}/messages/recent")
            wpp_bridge_live = r_wpp.status_code == 200
        except Exception:
            wpp_bridge_live = False

    # 3. Verifica Discord (Status real do Gateway + Canal #bot)
    discord_token = (creds.get("DISCORD_BOT_TOKEN") or settings.DISCORD_BOT_TOKEN).strip()
    discord_live = await get_discord_live_status()

    # 4. Chaves de API de IA em Nuvem
    google_key = (creds.get("GOOGLE_AI_STUDIO_API_KEY") or settings.GOOGLE_AI_STUDIO_API_KEY).strip()
    openai_key = (creds.get("OPENAI_CODE_API_KEY") or settings.OPENAI_CODE_API_KEY).strip()
    google_oauth = (creds.get("GOOGLE_CLIENT_ID") or settings.GOOGLE_CLIENT_ID).strip()

    # 5. Hardware Avell (Fans & RGB)
    try:
        hw_info = json.loads(get_fans_and_rgb_status())
        fan_mode = hw_info.get("fans", {}).get("mode", "balanced").upper()
        temps_dict = hw_info.get("temperatures", {})
        gpu_temp = int(round(temps_dict.get("gpu_temp_c") or 0))
        cpu_temp = int(round(temps_dict.get("cpu_temp_c") or 0))
        rgb_preset = hw_info.get("rgb", {}).get("preset", "ruinas_verde")
        hw_status_str = (
            f"[ruins.sage]■ Operando[/ruins.sage] "
            f"[ruins.muted](Fans: {fan_mode} | CPU: {cpu_temp}°C • GPU: {gpu_temp}°C | Runas RGB: {rgb_preset})[/ruins.muted]"
        )
    except Exception:
        hw_status_str = "[ruins.sage]■ Operando[/ruins.sage]"

    # Monta tabela de status no estilo Tábua de Pedra Antiga com Musgo
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="ruins.gold", justify="left", no_wrap=True)
    grid.add_column(justify="left")

    # Divisória superior de ruínas igual ao sidebar do Painel
    grid.add_row(
        "🌿 Identidade Arcana:",
        "[ruins.sage]Sophy[/ruins.sage] [ruins.gold](v2.1)[/ruins.gold] "
        "[ruins.muted]• Irmã mais nova da Sophya • Maga do Santuário ＜(´⌯  ̫⌯`)＞ 🪄[/ruins.muted]",
    )

    # Estado geral
    if settings.STRICT_OFFLINE_MODE:
        modo_str = "[ruins.ruby]■ SELO DO ERMITÃO ATIVO (100% ISOLADA NAS RUÍNAS LOCAIS)[/ruins.ruby]"
    else:
        modo_str = (
            f"[ruins.sage]■ SANTUÁRIO HÍBRIDO ATIVO[/ruins.sage] "
            f"[ruins.muted]({now_str} • {settings.TIMEZONE})[/ruins.muted]"
        )
    grid.add_row("❖ Status da Sophy:", modo_str)

    # Painel Desktop
    panel_url = f"http://{settings.GUI_HOST}:{settings.GUI_PORT}"
    if panel_online:
        grid.add_row(
            "🏛️  Painel das Ruínas:",
            f"[ruins.sage]■ Conectado & Aberto[/ruins.sage] ([link={panel_url}][ruins.gold]{panel_url}[/ruins.gold][/link])",
        )
    else:
        grid.add_row(
            "🏛️  Painel das Ruínas:",
            f"[ruins.amber]■ Despertando em segundo plano[/ruins.amber] ([ruins.muted]{panel_url}[/ruins.muted])",
        )

    # WhatsApp
    if wpp_bridge_live:
        wpp_str = f"[ruins.sage]■ Runa Ativa & Sincronizando[/ruins.sage] [ruins.muted](Bridge :{wpp_port} ativo)[/ruins.muted]"
    elif wpp_paired:
        wpp_str = f"[ruins.sage]■ Runa Pareada[/ruins.sage] [ruins.muted](Porta :{wpp_port} • Cache SQLite das Ruínas ativo)[/ruins.muted]"
    else:
        wpp_str = f"[ruins.amber]■ Em Repouso[/ruins.amber] [ruins.muted](Porta :{wpp_port} • Aguardando inscrição QR)[/ruins.muted]"
    grid.add_row("💬 Mensageiro WhatsApp:", wpp_str)

    # Discord
    d_mode = discord_live.get("mode")
    d_user = discord_live.get("bot_username", "Sophy")
    d_ch_id = discord_live.get("channel_id", "")
    d_ch_name = discord_live.get("channel_name", "bot")
    if d_mode == "NATIVE_GATEWAY":
        disc_str = (
            f"[ruins.sage]■ Conectada no Chat #{d_ch_name}[/ruins.sage] "
            f"[ruins.muted](@{d_user} • Canal {d_ch_id} • Gateway Online)[/ruins.muted]"
        )
    elif d_mode == "WEBHOOK_BRIDGE_ACTIVE":
        disc_str = (
            f"[ruins.sage]■ Conectada no Chat #{d_ch_name} (Ponte Ativa)[/ruins.sage] "
            f"[ruins.muted](@{d_user} • Canal {d_ch_id} respondendo em tempo real)[/ruins.muted]"
        )
    elif discord_token:
        disc_str = f"[ruins.amber]■ Aguardando Permissão no Servidor[/ruins.amber] [ruins.muted]({_mask_key(discord_token)})[/ruins.muted]"
    else:
        disc_str = "[ruins.ruby]■ Desconectado[/ruins.ruby] [ruins.muted](DISCORD_BOT_TOKEN ausente)[/ruins.muted]"
    grid.add_row("🔮 Ponte Discord:", disc_str)

    # Chaves de API e Motores de IA
    if google_key:
        g_status = (
            f"[ruins.sage]■ Canalizando Mana[/ruins.sage] [ruins.muted]({settings.GOOGLE_AI_MODEL} • {_mask_key(google_key)})[/ruins.muted]"
            if not settings.STRICT_OFFLINE_MODE
            else f"[ruins.amber]■ Selada (Modo Offline)[/ruins.amber] [ruins.muted]({_mask_key(google_key)})[/ruins.muted]"
        )
    else:
        g_status = "[ruins.ruby]■ Sem relíquia[/ruins.ruby]"
    grid.add_row("🗝️  Relíquia Google AI:", g_status)

    if openai_key:
        o_status = (
            f"[ruins.sage]■ Canalizando Mana[/ruins.sage] [ruins.muted]({settings.OPENAI_CODE_MODEL} • {_mask_key(openai_key)})[/ruins.muted]"
            if not settings.STRICT_OFFLINE_MODE
            else f"[ruins.amber]■ Selada (Modo Offline)[/ruins.amber] [ruins.muted]({_mask_key(openai_key)})[/ruins.muted]"
        )
    else:
        o_status = "[ruins.ruby]■ Sem relíquia[/ruins.ruby]"
    grid.add_row("🗝️  Relíquia OpenAI Code:", o_status)

    # Motor Local Ollama
    if ollama_ok:
        ollama_str = (
            f"[ruins.sage]■ Espírito Local Acordado (CUDA)[/ruins.sage] "
            f"[ruins.muted]({settings.OLLAMA_REASONING_MODEL} + {settings.OLLAMA_EMBED_MODEL})[/ruins.muted]"
        )
    else:
        ollama_str = "[ruins.amber]■ Em Repouso no Santuário[/ruins.amber] [ruins.muted](Usando Pontes de Nuvem)[/ruins.muted]"
    grid.add_row("🪄 Espírito Ollama:", ollama_str)

    # Grimório Obsidian & Google Sync & Hardware
    vault_ok = settings.OBSIDIAN_VAULT_PATH.exists()
    vault_str = (
        f"[ruins.sage]■ Grimório Aberto[/ruins.sage] [ruins.muted]({settings.OBSIDIAN_VAULT_PATH.name})[/ruins.muted]"
        if vault_ok
        else "[ruins.ruby]■ Grimório não encontrado[/ruins.ruby]"
    )
    if google_oauth:
        vault_str += " | [ruins.sage]Gmail/Drive OAuth Ativo[/ruins.sage]"
    grid.add_row("📖 Grimório Obsidian:", vault_str)
    grid.add_row("⚡ Cristal & Ventos Avell:", hw_status_str)

    console.print(
        Panel(
            grid,
            title="[ruins.gold]❖ SOPHY (v2.1) // SANTUÁRIO DAS RUÍNAS ANTIGAS [16-BIT] ❖[/ruins.gold]",
            subtitle="[ruins.muted]🌿 Digite para conversar no Altar • /status • /painel • /limpar • /sair 🌿[/ruins.muted]",
            border_style="#4d8a56",
            box=box.SQUARE,
            padding=(1, 2),
        )
    )


async def interactive_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Sophy (v2.1) — Maga do Santuário das Ruínas Antigas & IA Local Híbrida"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Apenas exibe o painel de conexões e status da Sophy sem iniciar o loop de chat.",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=None,
        help="Envia um único prompt para a Sophy e encerra.",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Inicia o Painel de Configurações Desktop em primeiro plano.",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Inicia a Sophy no terminal sem abrir automaticamente a janela do Painel Desktop.",
    )
    args = parser.parse_args()

    if args.gui:
        from gui.desktop_launcher import main as run_gui
        sys.argv = [sys.argv[0]]
        run_gui()
        return

    agent = LocalHybridAgent()

    if args.check:
        panel_ok = await ensure_panel_running_and_open(open_window=False)
        await render_sophy_startup_panel(panel_online=panel_ok)
        return

    if args.prompt:
        panel_ok = await ensure_panel_running_and_open(open_window=False)
        await render_sophy_startup_panel(panel_online=panel_ok)
        console.print(f"\n[ruins.gold]❖ usuário >[/ruins.gold] [ruins.text]{args.prompt}[/ruins.text]\n")
        try:
            reply = await agent.chat(args.prompt, on_tool_event=log_tool_execution)
            console.print(
                Panel(
                    Markdown(reply),
                    title=f"[ruins.gold]🪄 Sophy • v2.1[/ruins.gold] [ruins.muted]({agent.last_provider_used})[/ruins.muted]",
                    border_style="#4d8a56",
                    box=box.SQUARE,
                    padding=(0, 2),
                )
            )
        except Exception as exc:
            console.print(
                f"[ruins.ruby]Erro ao comunicar com o grimório da Sophy:[/ruins.ruby] {exc}"
            )
            sys.exit(1)
        return

    # Modo interativo padrão: garante o servidor do Painel ativo, inicia o bot Discord e abre a janela do Painel junto!
    set_code_confirmation_callback(ask_user_code_diff_confirmation)
    sophy_discord_service.start_background()
    panel_ok = await ensure_panel_running_and_open(open_window=not args.no_gui)
    await render_sophy_startup_panel(panel_online=panel_ok)

    while True:
        try:
            user_input = console.input("\n[ruins.gold]❖ usuário[/ruins.gold] [ruins.sage]>[/ruins.sage] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print(
                "\n[ruins.sage]🪄 Até logo, usuário! Vou ficar cuidando dos grimórios aqui no santuário das ruínas! ＜(´⌯  ̫⌯`)＞ 🌿✨[/ruins.sage]"
            )
            break

        if not user_input:
            continue
        if user_input.lower() in {"/sair", "sair", "exit", "quit"}:
            console.print(
                "\n[ruins.sage]🪄 Até mais, usuário! Se precisar de algum feitiço ou pergaminho é só me chamar! ＜(´⌯  ̫⌯`)＞ 💖🌿[/ruins.sage]"
            )
            break
        if user_input.lower() in {"/limpar", "limpar", "cls", "clear"}:
            agent.reset_history()
            console.print(
                "[ruins.muted]✨ Pergaminho da conversa renovado! Qual grimório vamos explorar agora? ＜(´⌯  ̫⌯`)＞ 📖[/ruins.muted]"
            )
            continue
        if user_input.lower() in {"/status", "status"}:
            panel_ok = await ensure_panel_running_and_open(open_window=False)
            await render_sophy_startup_panel(panel_online=panel_ok)
            continue
        if user_input.lower() in {"/painel", "painel"}:
            await ensure_panel_running_and_open(open_window=True)
            console.print("[ruins.muted]🏛️  Janela do Painel das Ruínas da Sophy aberta![/ruins.muted]")
            continue

        try:
            reply = await agent.chat(user_input, on_tool_event=log_tool_execution)
            console.print(
                Panel(
                    Markdown(reply),
                    title=f"[ruins.gold]🪄 Sophy • v2.1[/ruins.gold] [ruins.muted]({agent.last_provider_used})[/ruins.muted]",
                    border_style="#4d8a56",
                    box=box.SQUARE,
                    padding=(0, 2),
                )
            )
        except Exception as exc:
            console.print(
                f"[ruins.ruby]Falha ao conjurar resposta da Sophy:[/ruins.ruby] {exc}"
            )


if __name__ == "__main__":
    asyncio.run(interactive_cli())
