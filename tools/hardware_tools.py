"""
Ferramentas de Controle Físico do Notebook (seu notebook - Chassi Uniwill):
- Controle de Fans via `avell-fan` (ioctl em `/dev/tuxedo_io`, pacote `tuxedo-drivers`) e `powerprofilesctl`.
- Controle de Iluminação RGB do Teclado (`/sys/class/leds/rgb:kbd_backlight`, `multi_intensity`
  0-255 e `brightness` 0-4) e fallback `openrgb` para periféricos extras.
- Auto-ajuste inteligente pela Sophy (v2.1) de acordo com temperatura da CPU/GPU, carga de trabalho ou horário.

IMPORTANTE: nunca escrever em `/sys/class/hwmon/*/pwm*` nem carregar `uniwill_laptop` neste notebook.
Em 2026-11-01 isso derrubou o kernel (ver ~/AVELL_HARDWARE.md).
"""

from datetime import datetime
import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from config import BASE_DIR, settings

STATE_FILE_PATH = BASE_DIR / "database" / "hardware_state.json"
ENABLE_SCRIPT_PATH = BASE_DIR / "scripts" / "enable_avell_hardware.sh"

AVELL_FAN_BIN = shutil.which("avell-fan") or "/usr/local/bin/avell-fan"
TUXEDO_IO_DEV = Path("/dev/tuxedo_io")
FAN_MIN_PERCENT = 25  # abaixo disso o avell-fan recusa (a fan pararia)
FAN_GUARD_CPU_C = 90  # no modo manual, acima disso as fans voltam para a curva automática do EC
FAN_GUARD_GPU_C = 85

# Paleta de Presets de Cores RGB do Santuário das Ruínas
RGB_PRESETS: Dict[str, Tuple[int, int, int, str]] = {
    "ruinas_verde": (77, 138, 86, "#4d8a56"),
    "verde": (77, 138, 86, "#4d8a56"),
    "musgo": (77, 138, 86, "#4d8a56"),
    "salvia": (134, 207, 144, "#86cf90"),
    "folha": (98, 171, 108, "#62ab6c"),
    "menta": (134, 207, 144, "#86cf90"),
    "ouro_antigo": (216, 184, 106, "#d8b86a"),
    "dourado": (216, 184, 106, "#d8b86a"),
    "reliquia": (216, 184, 106, "#d8b86a"),
    "ambar": (207, 160, 78, "#cfa04e"),
    "fogueira": (224, 122, 60, "#e07a3c"),
    "laranja": (224, 122, 60, "#e07a3c"),
    "cristal_mana": (92, 168, 230, "#5ca8e6"),
    "azul": (92, 168, 230, "#5ca8e6"),
    "gelo": (130, 210, 245, "#82d2f5"),
    "arcano": (157, 107, 224, "#9d6be0"),
    "roxo": (157, 107, 224, "#9d6be0"),
    "ametista": (157, 107, 224, "#9d6be0"),
    "rubi": (214, 69, 69, "#d64545"),
    "vermelho": (214, 69, 69, "#d64545"),
    "alerta": (214, 69, 69, "#d64545"),
    "luz_lunar": (228, 240, 223, "#e4f0df"),
    "branco": (228, 240, 223, "#e4f0df"),
    "rosa_cerejeira": (235, 145, 175, "#eb91af"),
    "rosa": (235, 145, 175, "#eb91af"),
    "off": (0, 0, 0, "#000000"),
    "desligado": (0, 0, 0, "#000000"),
}


def _load_saved_state() -> Dict[str, Any]:
    default_state = {
        "fan_mode": "balanced",
        "fan_speed_percent": 60,
        "rgb_preset": "ruinas_verde",
        "rgb_hex": "#4d8a56",
        "rgb_r": 77,
        "rgb_g": 138,
        "rgb_b": 86,
        "rgb_brightness": 80,
        "rgb_effect": "static",
        "updated_at": "",
    }
    if STATE_FILE_PATH.exists():
        try:
            loaded = json.loads(STATE_FILE_PATH.read_text(encoding="utf-8"))
            default_state.update(loaded)
        except Exception:
            pass
    return default_state


def _save_state(updates: Dict[str, Any]) -> Dict[str, Any]:
    state = _load_saved_state()
    state.update(updates)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def _fan_control_ready() -> bool:
    """`/dev/tuxedo_io` existe e o usuário atual (grupo `wheel`) pode usá-lo."""
    return TUXEDO_IO_DEV.exists() and os.access(TUXEDO_IO_DEV, os.R_OK | os.W_OK)


def _run_avell_fan(*args: str, timeout: float = 5.0) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Executa `avell-fan --json [args]` e devolve (status, erro).
    Status: {"cpu": {"temp_c": int, "fan_pct": int}, "gpu": {...}}.
    """
    if not _fan_control_ready():
        return None, "/dev/tuxedo_io indisponível (driver TUXEDO não carregado?)"
    try:
        res = subprocess.run(
            [AVELL_FAN_BIN, "--json", *args], capture_output=True, text=True, timeout=timeout
        )
    except Exception as exc:
        return None, str(exc)
    if res.returncode != 0:
        return None, (res.stderr or res.stdout).strip() or f"avell-fan saiu com código {res.returncode}"
    try:
        return json.loads(res.stdout), None
    except ValueError:
        return None, f"Saída inesperada do avell-fan: {res.stdout.strip()[:120]}"


def _find_kbd_backlight_led() -> Optional[Path]:
    """Localiza o LED RGB do teclado (`rgb:kbd_backlight` do driver TUXEDO) em `/sys/class/leds/`."""
    for c_str in sorted(glob.glob("/sys/class/leds/*kbd_backlight*")):
        p = Path(c_str)
        if p.exists():
            return p
    return None


def _led_max_intensity(led_dir: Path) -> int:
    """Escala de cada canal de cor do LED (255 no driver TUXEDO)."""
    try:
        return int((led_dir / "multi_max_intensity").read_text(encoding="utf-8").split()[0])
    except Exception:
        return 255


def _get_cpu_and_gpu_temps() -> Dict[str, float]:
    """Lê a temperatura atual da CPU (`coretemp`) e da GPU (`nvidia-smi`)."""
    cpu_temp = 0.0
    gpu_temp = 0.0

    for p_str in glob.glob("/sys/class/hwmon/hwmon*"):
        p = Path(p_str)
        try:
            name = (p / "name").read_text(encoding="utf-8").strip().lower()
            if name in ("coretemp", "k10temp", "acpitz"):
                t_file = p / "temp1_input"
                if t_file.exists():
                    val = float(t_file.read_text(encoding="utf-8").strip()) / 1000.0
                    if val > cpu_temp:
                        cpu_temp = round(val, 1)
        except Exception:
            continue

    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            text=True,
            timeout=2.0,
        ).strip()
        gpu_temp = float(out.splitlines()[0].strip())
    except Exception:
        pass

    return {"cpu_temp_c": cpu_temp, "gpu_temp_c": gpu_temp}


def _get_power_profile() -> str:
    """Obtém o perfil de energia ativo via `powerprofilesctl`."""
    if shutil.which("powerprofilesctl"):
        try:
            return subprocess.check_output(["powerprofilesctl", "get"], text=True, timeout=2.0).strip()
        except Exception:
            pass
    return "unknown"


def _set_power_profile(profile_name: str) -> bool:
    """Altera o perfil de energia via `powerprofilesctl` (power-saver, balanced, performance)."""
    if not shutil.which("powerprofilesctl"):
        return False
    try:
        subprocess.run(["powerprofilesctl", "set", profile_name], check=True, timeout=3.0)
        return True
    except Exception:
        return False


def _parse_color_input(color_or_preset: str) -> Tuple[int, int, int, str, str]:
    """Converte o nome de um preset ou código Hex (#RRGGBB) em (R, G, B, hex, nome_preset)."""
    clean = color_or_preset.strip().lower().replace(" ", "_").replace("-", "_")
    if clean in RGB_PRESETS:
        r, g, b, hx = RGB_PRESETS[clean]
        return r, g, b, hx, clean

    hex_cand = color_or_preset.strip().lstrip("#")
    if len(hex_cand) == 6:
        try:
            r = int(hex_cand[0:2], 16)
            g = int(hex_cand[2:4], 16)
            b = int(hex_cand[4:6], 16)
            return r, g, b, f"#{hex_cand.lower()}", "custom"
        except ValueError:
            pass

    # Fallback para Verde Ruínas
    r, g, b, hx = RGB_PRESETS["ruinas_verde"]
    return r, g, b, hx, "ruinas_verde"


def activate_avell_hardware_driver() -> Dict[str, Any]:
    """
    Garante que os módulos do `tuxedo-drivers` (fans + RGB) estão carregados, rodando
    `scripts/enable_avell_hardware.sh` via `pkexec` (janela gráfica de autenticação do Linux) se preciso.
    """
    if _fan_control_ready() and _find_kbd_backlight_led():
        return {
            "ok": True,
            "driver_loaded": True,
            "message": "Driver TUXEDO (tuxedo-drivers) já está carregado: fans e RGB operacionais!",
        }

    if not ENABLE_SCRIPT_PATH.exists():
        return {
            "ok": False,
            "driver_loaded": False,
            "message": f"Script não encontrado em {ENABLE_SCRIPT_PATH}.",
        }

    try:
        subprocess.run(["pkexec", "/bin/bash", str(ENABLE_SCRIPT_PATH)], check=True, timeout=45.0)
        # Re-aplica a última cor salva; as fans ficam na curva automática do EC
        saved = _load_saved_state()
        control_keyboard_rgb(
            saved.get("rgb_hex", "#4d8a56"),
            saved.get("rgb_brightness", 80),
            saved.get("rgb_effect", "static"),
        )
        return {
            "ok": True,
            "driver_loaded": True,
            "message": "Driver TUXEDO (seu notebook) ativado! Fans na curva automática do EC.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "driver_loaded": False,
            "message": (
                f"Não foi possível ativar via pkexec automaticamente ({exc}). "
                f"Você também pode rodar no terminal: sudo {ENABLE_SCRIPT_PATH}"
            ),
        }


# =====================================================================
# FERRAMENTAS EXPOSTAS PARA A SOPHY — SOPHY v2.1 (E ENDPOINTS DO PAINEL)
# =====================================================================

def get_fans_and_rgb_status() -> str:
    """
    Retorna o status completo das ventoinhas (Fans), temperaturas de CPU/GPU,
    perfil térmico ativo e estado atual da iluminação RGB do teclado do notebook
    seu notebook. Use esta ferramenta antes ou depois de ajustar fans ou RGB.
    As fans são informadas em porcentagem (este driver não mede RPM).
    """
    saved = _load_saved_state()
    temps = _get_cpu_and_gpu_temps()
    power_prof = _get_power_profile()
    led_dir = _find_kbd_backlight_led()
    fan_data, fan_error = _run_avell_fan()

    # Trava de segurança: velocidade manual baixa não pode segurar o notebook quente
    safety_override = False
    if fan_data and saved.get("fan_mode") == "manual":
        cpu_peak = max(temps["cpu_temp_c"], fan_data["cpu"]["temp_c"])
        gpu_peak = max(temps["gpu_temp_c"], fan_data["gpu"]["temp_c"])
        if cpu_peak >= FAN_GUARD_CPU_C or gpu_peak >= FAN_GUARD_GPU_C:
            fan_data, fan_error = _run_avell_fan("auto")
            saved = _save_state({"fan_mode": "balanced"})
            safety_override = True

    hw_rgb: Dict[str, Any] = {
        "sysfs_led_path": str(led_dir) if led_dir else None,
        "preset": saved.get("rgb_preset", "ruinas_verde"),
        "color_hex": saved.get("rgb_hex", "#4d8a56"),
        "rgb": [saved.get("rgb_r", 77), saved.get("rgb_g", 138), saved.get("rgb_b", 86)],
        "brightness_percent": saved.get("rgb_brightness", 80),
        "effect": saved.get("rgb_effect", "static"),
    }

    if led_dir:
        try:
            max_b = int((led_dir / "max_brightness").read_text(encoding="utf-8").strip())
            cur_b = int((led_dir / "brightness").read_text(encoding="utf-8").strip())
            hw_rgb["brightness_percent"] = round((cur_b / max_b) * 100) if max_b else 0
            if (led_dir / "multi_intensity").exists():
                max_i = _led_max_intensity(led_dir)
                parts = [
                    round(int(x) * 255 / max_i)
                    for x in (led_dir / "multi_intensity").read_text(encoding="utf-8").strip().split()
                ]
                if len(parts) == 3:
                    hw_rgb["rgb"] = parts
                    hw_rgb["color_hex"] = f"#{parts[0]:02x}{parts[1]:02x}{parts[2]:02x}"
        except Exception:
            pass

    status_payload = {
        "laptop_model": "Avell High Performance STORM BS (Uniwill EC)",
        "hardware_driver": "tuxedo-drivers",
        "uniwill_driver_loaded": bool(fan_data and led_dir),
        "temperatures": temps,
        "fans": {
            "mode": saved.get("fan_mode", "balanced"),
            "speed_percent": fan_data["cpu"]["fan_pct"] if fan_data else saved.get("fan_speed_percent", 60),
            "power_profile_daemon": power_prof,
            "cpu_fan_percent": fan_data["cpu"]["fan_pct"] if fan_data else None,
            "gpu_fan_percent": fan_data["gpu"]["fan_pct"] if fan_data else None,
            "ec_cpu_temp_c": fan_data["cpu"]["temp_c"] if fan_data else None,
            "ec_gpu_temp_c": fan_data["gpu"]["temp_c"] if fan_data else None,
            "safety_override": safety_override,
            "fan_error": fan_error,
        },
        "keyboard_rgb": hw_rgb,
        "available_rgb_presets": list(RGB_PRESETS.keys()),
    }
    return json.dumps(status_payload, ensure_ascii=False, indent=2)


def control_notebook_fans(mode: str = "balanced", speed_percent: int = 60) -> str:
    """
    Controla a velocidade das ventoinhas (Fans) e o perfil térmico do notebook seu notebook.
    Parâmetros:
    - `mode`: 'silent' (curva automática do EC + perfil economia, para estudo/leitura/noite),
      'balanced' (curva automática do EC, uso diário), 'performance' (fans fixas em 80% para
      jogos/IA/compilação), 'turbo' (fans em 100% para resfriamento rápido) ou 'manual'
      (porcentagem fixa em `speed_percent`).
    - `speed_percent`: velocidade de 25 a 100 (usada só quando mode='manual').
    No modo manual, se a CPU passar de 90°C ou a GPU de 85°C, as fans voltam sozinhas para a curva automática.
    """
    mode_clean = mode.strip().lower()
    mode_aliases = {
        "silencioso": "silent",
        "quiet": "silent",
        "eco": "silent",
        "equilibrado": "balanced",
        "auto": "balanced",
        "normal": "balanced",
        "desempenho": "performance",
        "alto": "performance",
        "gaming": "performance",
        "max": "turbo",
        "maximo": "turbo",
        "máximo": "turbo",
        "full": "turbo",
    }
    mode_norm = mode_aliases.get(mode_clean, mode_clean)
    if mode_norm not in ("silent", "balanced", "performance", "turbo", "manual"):
        mode_norm = "balanced"

    clamped_pct = max(FAN_MIN_PERCENT, min(100, int(speed_percent)))

    # 1. Define o Power Profile do sistema e a velocidade fixa (None = curva automática do EC)
    if mode_norm == "silent":
        target_pp, fixed_pct = "power-saver", None
    elif mode_norm == "balanced":
        target_pp, fixed_pct = "balanced", None
    elif mode_norm == "performance":
        target_pp, fixed_pct = "performance", 80
    elif mode_norm == "turbo":
        target_pp, fixed_pct = "performance", 100
    else:  # manual
        target_pp = "performance" if clamped_pct >= 75 else ("balanced" if clamped_pct >= 45 else "power-saver")
        fixed_pct = clamped_pct

    pp_applied = _set_power_profile(target_pp)

    # 2. Aplica nas fans via avell-fan (`auto` devolve o controle ao EC).
    # O primeiro valor fixo depois do `auto` reinicializa as tabelas do EC e leva ~14 s.
    if fixed_pct:
        fan_data, fan_error = _run_avell_fan(str(fixed_pct), timeout=30.0)
    else:
        fan_data, fan_error = _run_avell_fan("auto")
    effective_pct = fixed_pct or (fan_data["cpu"]["fan_pct"] if fan_data else None)

    updates: Dict[str, Any] = {"fan_mode": mode_norm}
    if effective_pct is not None:
        updates["fan_speed_percent"] = effective_pct
    state = _save_state(updates)

    pp_text = f"perfil '{target_pp}'" if pp_applied else "perfil de energia inalterado"
    if not fan_data:
        fan_note = (
            f"As fans não responderam ({fan_error}); {pp_text}. "
            "Clique em [ATIVAR DRIVER AVELL] no Painel."
        )
    elif fixed_pct:
        fan_note = f"Fans fixadas em {fixed_pct}% ({pp_text})."
    else:
        fan_note = f"Fans na curva automática do EC ({pp_text})."

    return json.dumps(
        {
            "status": "OK" if fan_data else "PARCIAL",
            "fan_mode": state["fan_mode"],
            "fan_speed_percent": state["fan_speed_percent"],
            "power_profile_set": target_pp if pp_applied else "unchanged",
            "fan_control_applied": bool(fan_data),
            "uniwill_driver_loaded": _fan_control_ready(),
            "fan_note": fan_note,
        },
        ensure_ascii=False,
        indent=2,
    )


def control_keyboard_rgb(
    color_or_preset: str = "ruinas_verde",
    brightness_percent: int = 80,
    effect: str = "static",
) -> str:
    """
    Altera a cor e o brilho do teclado RGB do notebook seu notebook.
    Parâmetros:
    - `color_or_preset`: Nome de um preset ('ruinas_verde', 'salvia', 'ouro_antigo', 'fogueira',
      'cristal_mana', 'arcano', 'rubi', 'luz_lunar', 'rosa_cerejeira', 'off') ou código Hex ('#4d8a56').
    - `brightness_percent`: Intensidade do brilho de 0 a 100 (o teclado tem 4 níveis reais).
    - `effect`: 'static' (cor sólida) ou 'off' (apagado). Este teclado não tem efeito arco-íris.
    """
    effect_requested = effect.strip().lower()
    if color_or_preset.strip().lower() in ("off", "desligado", "apagar") or effect_requested == "off":
        r, g, b, hx, preset_name = 0, 0, 0, "#000000", "off"
        clamped_bright = 0
        effect_clean = "off"
    else:
        r, g, b, hx, preset_name = _parse_color_input(color_or_preset)
        clamped_bright = max(0, min(100, int(brightness_percent)))
        effect_clean = "static"

    led_dir = _find_kbd_backlight_led()
    sysfs_applied = False
    sysfs_error = None

    if led_dir:
        try:
            max_b = 4
            if (led_dir / "max_brightness").exists():
                max_b = int((led_dir / "max_brightness").read_text(encoding="utf-8").strip())

            scaled_b = int(round((clamped_bright / 100.0) * max_b))
            if clamped_bright > 0:
                scaled_b = max(1, scaled_b)  # brilho baixo não deve apagar o teclado

            if (led_dir / "multi_intensity").exists() and effect_clean != "off":
                max_i = _led_max_intensity(led_dir)
                (led_dir / "multi_intensity").write_text(
                    f"{r * max_i // 255} {g * max_i // 255} {b * max_i // 255}\n", encoding="utf-8"
                )

            if (led_dir / "brightness").exists():
                (led_dir / "brightness").write_text(f"{scaled_b}\n", encoding="utf-8")

            sysfs_applied = True
        except Exception as exc:
            sysfs_error = str(exc)

    # Tenta também OpenRGB se houver periféricos RGB extras conectados
    openrgb_applied = False
    if shutil.which("openrgb") and effect_clean != "off":
        try:
            hex_no_hash = hx.lstrip("#")
            res = subprocess.run(
                [
                    "openrgb",
                    "--noautoconnect",
                    "--color",
                    hex_no_hash,
                    "--brightness",
                    str(clamped_bright),
                ],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if res.returncode == 0:
                openrgb_applied = True
        except Exception:
            pass

    state = _save_state(
        {
            "rgb_preset": preset_name,
            "rgb_hex": hx,
            "rgb_r": r,
            "rgb_g": g,
            "rgb_b": b,
            "rgb_brightness": clamped_bright,
            "rgb_effect": effect_clean,
        }
    )

    if sysfs_applied:
        rgb_note = "Cor e brilho aplicados diretamente nos LEDs do teclado!"
        if effect_requested == "rainbow":
            rgb_note += " (Este teclado não tem efeito arco-íris; aplicada cor fixa.)"
    elif not led_dir:
        rgb_note = (
            "Configuração de cor salva! Para aplicar nos LEDs do seu notebook, "
            "clique em [ATIVAR DRIVER AVELL] no Painel para carregar o driver TUXEDO."
        )
    else:
        rgb_note = f"Aviso LED sysfs: {sysfs_error}"

    return json.dumps(
        {
            "status": "OK",
            "preset": state["rgb_preset"],
            "color_hex": state["rgb_hex"],
            "rgb": [r, g, b],
            "brightness_percent": state["rgb_brightness"],
            "effect": state["rgb_effect"],
            "sysfs_led_applied": sysfs_applied,
            "openrgb_applied": openrgb_applied,
            "uniwill_driver_loaded": bool(led_dir),
            "rgb_note": rgb_note,
        },
        ensure_ascii=False,
        indent=2,
    )


def auto_adjust_hardware_by_context(reason: str = "monitoramento_automatico") -> str:
    """
    Avalia automaticamente a temperatura da CPU e da GPU (RTX 4050) e o horário atual
    para ajustar de forma inteligente as ventoinhas (Fans) e a cor/brilho do RGB do teclado.
    A Sophy (v2.1) deve chamar esta ferramenta quando:
    - O usuário pedir para ela cuidar/ajustar automaticamente o notebook;
    - A temperatura da GPU/CPU estiver alta ou durante tarefas pesadas;
    - For horário noturno/estudo e o usuário quiser um ambiente calmo.
    """
    temps = _get_cpu_and_gpu_temps()
    cpu_t = temps["cpu_temp_c"]
    gpu_t = temps["gpu_temp_c"]
    peak_t = max(cpu_t, gpu_t)

    try:
        now_hour = datetime.now(ZoneInfo(settings.TIMEZONE)).hour
    except Exception:
        now_hour = datetime.now().hour

    if peak_t >= 80.0:
        fan_res = json.loads(control_notebook_fans(mode="turbo", speed_percent=100))
        rgb_res = json.loads(
            control_keyboard_rgb(color_or_preset="rubi", brightness_percent=90, effect="static")
        )
        decision = (
            f"Alerta térmico detectado (Pico: {peak_t}°C). Fans colocadas em TURBO (100%) "
            "e teclado em tom Rubi para sinalizar resfriamento intenso."
        )
    elif peak_t >= 68.0:
        fan_res = json.loads(control_notebook_fans(mode="performance", speed_percent=80))
        rgb_res = json.loads(
            control_keyboard_rgb(color_or_preset="cristal_mana", brightness_percent=75, effect="static")
        )
        decision = (
            f"Temperatura elevada em atividade ({peak_t}°C). Fans ajustadas para DESEMPENHO (80%) "
            "e teclado em azul Cristal de Mana."
        )
    elif now_hour >= 22 or now_hour < 6:
        fan_res = json.loads(control_notebook_fans(mode="silent", speed_percent=35))
        rgb_res = json.loads(
            control_keyboard_rgb(color_or_preset="ouro_antigo", brightness_percent=35, effect="static")
        )
        decision = (
            f"Horário noturno tranquilo ({now_hour}h) e temperaturas amenas ({peak_t}°C). "
            "Fans em modo SILENCIOSO e luz suave Ouro Antigo (35%) para não cansar a vista."
        )
    else:
        fan_res = json.loads(control_notebook_fans(mode="balanced", speed_percent=55))
        rgb_res = json.loads(
            control_keyboard_rgb(color_or_preset="ruinas_verde", brightness_percent=75, effect="static")
        )
        decision = (
            f"Temperaturas estáveis ({peak_t}°C). Fans em modo EQUILIBRADO e teclado no "
            "tom Verde das Ruínas (75%)."
        )

    return json.dumps(
        {
            "status": "AUTO_ADJUSTED",
            "reason": reason,
            "temperatures": temps,
            "decision_summary": decision,
            "fan_applied": fan_res,
            "rgb_applied": rgb_res,
        },
        ensure_ascii=False,
        indent=2,
    )


HARDWARE_TOOLS = [
    get_fans_and_rgb_status,
    control_notebook_fans,
    control_keyboard_rgb,
    auto_adjust_hardware_by_context,
]
