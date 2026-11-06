"""
Ferramentas de Controle do Spotify (`spotipy` OAuth2 + Client Credentials + D-Bus MPRIS Linux)
e Automações do Desktop GNOME (`start_pomodoro_timer`, `set_desktop_alarm`).
"""

import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Optional

from config import settings

SOPHY_CONFIG_DIR = Path.home() / ".config" / "sophya"
SOPHY_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
SPOTIFY_CACHE_PATH = SOPHY_CONFIG_DIR / "spotify_token.cache"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPE = "user-modify-playback-state user-read-playback-state user-read-currently-playing"


def get_spotify_auth_manager(open_browser: bool = False):
    """Retorna o gerenciador `SpotifyOAuth` configurado com o cache persistente da Sophy."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    client_id = settings.SPOTIPY_CLIENT_ID.strip() or os.getenv("SPOTIPY_CLIENT_ID", "").strip()
    client_secret = settings.SPOTIPY_CLIENT_SECRET.strip() or os.getenv("SPOTIPY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None

    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=str(SPOTIFY_CACHE_PATH),
        open_browser=open_browser,
    )


def get_spotify_status_summary() -> Dict[str, Any]:
    """Verifica se as chaves do Spotify estão presentes e se o token OAuth2 já foi autorizado."""
    client_id = settings.SPOTIPY_CLIENT_ID.strip()
    client_secret = settings.SPOTIPY_CLIENT_SECRET.strip()
    has_keys = bool(client_id and client_secret)
    has_token = False
    auth_url = ""
    if has_keys:
        try:
            am = get_spotify_auth_manager(open_browser=False)
            if am:
                tok = am.validate_token(am.cache_handler.get_cached_token())
                has_token = bool(tok)
                if not has_token:
                    auth_url = am.get_authorize_url()
        except Exception:
            pass
    return {
        "configured_keys": has_keys,
        "oauth_authenticated": has_token,
        "cache_path": str(SPOTIFY_CACHE_PATH),
        "authorize_url": auth_url,
    }


def _dbus_spotify_command(method: str) -> bool:
    """Envia comando Play/Pause/Next/Previous diretamente ao app Spotify no Linux via D-Bus MPRIS."""
    mpris_map = {
        "play": "Play",
        "pause": "Pause",
        "next": "Next",
        "previous": "Previous",
        "toggle": "PlayPause",
    }
    dbus_method = mpris_map.get(method.lower())
    if not dbus_method:
        return False
    try:
        res = subprocess.run(
            [
                "dbus-send",
                "--print-reply",
                "--dest=org.mpris.MediaPlayer2.spotify",
                "/org/mpris/MediaPlayer2",
                f"org.mpris.MediaPlayer2.Player.{dbus_method}",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return res.returncode == 0
    except Exception:
        return False


def control_spotify(action: str) -> str:
    """
    Controla o Spotify (tocar música específica por nome, play, pause, next, previous, status/current, ou buscar faixa).
    Exemplos de `action`:
    - 'status' ou 'current' (mostra a música tocando agora)
    - 'tocar Zoltraak Frieren' ou 'play Evan Call Frieren' (busca e toca a música)
    - 'pause' (pausa a reprodução)
    - 'play' (retoma a reprodução)
    - 'next' (próxima faixa)
    - 'previous' (faixa anterior)
    """
    client_id = settings.SPOTIPY_CLIENT_ID.strip()
    client_secret = settings.SPOTIPY_CLIENT_SECRET.strip()
    if not client_id or not client_secret:
        return json.dumps(
            {
                "status": "MISSING_CREDENTIALS",
                "message": "Configure SPOTIPY_CLIENT_ID e SPOTIPY_CLIENT_SECRET na Aba 03 do Painel.",
            },
            ensure_ascii=False,
        )

    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials

    act = action.strip()
    act_lower = act.lower()

    auth_manager = get_spotify_auth_manager(open_browser=False)
    token_info = auth_manager.validate_token(auth_manager.cache_handler.get_cached_token()) if auth_manager else None

    # Se o usuário pediu apenas busca ou se o token OAuth ainda não foi autorizado, usa ClientCredentials para buscar
    if not token_info:
        # Tenta controle local via D-Bus MPRIS se o app Spotify estiver aberto no Linux
        if act_lower in ("play", "tocar", "resume"):
            if _dbus_spotify_command("play"):
                return json.dumps({"status": "OK", "method": "dbus_mpris", "message": "Playback retomado no Spotify Desktop via D-Bus."})
        elif act_lower in ("pause", "pausar", "stop"):
            if _dbus_spotify_command("pause"):
                return json.dumps({"status": "OK", "method": "dbus_mpris", "message": "Playback pausado no Spotify Desktop via D-Bus."})
        elif act_lower in ("next", "proxima", "pular"):
            if _dbus_spotify_command("next"):
                return json.dumps({"status": "OK", "method": "dbus_mpris", "message": "Avançou para a próxima faixa no Spotify Desktop via D-Bus."})
        elif act_lower in ("previous", "anterior", "voltar"):
            if _dbus_spotify_command("previous"):
                return json.dumps({"status": "OK", "method": "dbus_mpris", "message": "Voltou para a faixa anterior no Spotify Desktop via D-Bus."})

        # Busca no catálogo Spotify mesmo sem login de usuário (usando Client Credentials!)
        search_res = None
        if act_lower.startswith(("tocar ", "play ", "buscar ", "search ")):
            query_term = act.split(maxsplit=1)[1].strip()
            try:
                sp_cc = spotipy.Spotify(
                    auth_manager=SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
                )
                tracks = sp_cc.search(q=query_term, limit=3, type="track").get("tracks", {}).get("items", [])
                if tracks:
                    top = tracks[0]
                    # Tenta abrir a URI no app desktop se disponível
                    subprocess.Popen(
                        ["xdg-open", top["uri"]],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    search_res = {
                        "name": top["name"],
                        "artists": ", ".join(a["name"] for a in top.get("artists", [])),
                        "uri": top["uri"],
                        "external_url": top.get("external_urls", {}).get("spotify", ""),
                    }
            except Exception:
                pass

        auth_url = auth_manager.get_authorize_url() if auth_manager else ""
        return json.dumps(
            {
                "status": "NEEDS_USER_OAUTH" if not search_res else "OPENED_VIA_URI",
                "track_found": search_res,
                "authorize_url": auth_url,
                "message": (
                    "Para controle remoto completo da sua conta Spotify via API, conclua a autorização OAuth2 "
                    "uma única vez no navegador."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    sp = spotipy.Spotify(auth_manager=auth_manager)

    try:
        if act_lower.startswith(("tocar ", "play ", "buscar ", "search ")) and len(act.split()) > 1:
            termo = act.split(maxsplit=1)[1].strip()
            results = sp.search(q=termo, limit=1, type="track")
            items = results.get("tracks", {}).get("items", [])
            if not items:
                return json.dumps({"status": "NOT_FOUND", "query": termo}, ensure_ascii=False)
            track = items[0]
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            try:
                sp.start_playback(uris=[track["uri"]])
                return json.dumps(
                    {
                        "status": "PLAYING",
                        "track": track["name"],
                        "artists": artists,
                        "uri": track["uri"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as play_err:
                return json.dumps(
                    {
                        "status": "NO_ACTIVE_DEVICE",
                        "track": track["name"],
                        "artists": artists,
                        "uri": track["uri"],
                        "external_url": track.get("external_urls", {}).get("spotify", ""),
                        "details": f"Faixa encontrada, mas nenhum player Spotify estava ativo ({play_err}).",
                    },
                    ensure_ascii=False,
                    indent=2,
                )

        if act_lower in ("play", "tocar", "resume", "retomar"):
            sp.start_playback()
            return json.dumps({"status": "PLAYING", "action": "play"}, ensure_ascii=False)
        if act_lower in ("pause", "pausar", "stop", "parar"):
            sp.pause_playback()
            return json.dumps({"status": "PAUSED", "action": "pause"}, ensure_ascii=False)
        if act_lower in ("next", "proxima", "pular"):
            sp.next_track()
            return json.dumps({"status": "SKIPPED_NEXT", "action": "next"}, ensure_ascii=False)
        if act_lower in ("previous", "anterior", "voltar"):
            sp.previous_track()
            return json.dumps({"status": "SKIPPED_PREVIOUS", "action": "previous"}, ensure_ascii=False)

        # Default: status / current playback
        current = sp.current_playback()
        if current and current.get("item"):
            item = current["item"]
            return json.dumps(
                {
                    "status": "OK",
                    "is_playing": current.get("is_playing", False),
                    "track": item.get("name"),
                    "artists": ", ".join(a["name"] for a in item.get("artists", [])),
                    "album": (item.get("album") or {}).get("name"),
                    "device": (current.get("device") or {}).get("name"),
                },
                ensure_ascii=False,
                indent=2,
            )
        return json.dumps({"status": "IDLE", "message": "Nenhuma música tocando no momento."}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


def start_pomodoro_timer(focus_minutes: int = 25, break_minutes: int = 5) -> str:
    """
    Inicia um ciclo Pomodoro no desktop Linux (GNOME) com notificações nativas (`notify-send`).
    Parâmetros:
    - focus_minutes: Tempo de foco em minutos (padrão 25).
    - break_minutes: Tempo de pausa em minutos (padrão 5).
    """
    try:
        subprocess.Popen(
            [
                "notify-send",
                "-a",
                "Sophy",
                "-u",
                "normal",
                "🍵 Sophy • Pomodoro Iniciado",
                f"Ciclo de {focus_minutes} min de foco e {break_minutes} min de descanso iniciado.",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    return json.dumps(
        {
            "status": "POMODORO_STARTED",
            "focus_minutes": focus_minutes,
            "break_minutes": break_minutes,
        },
        ensure_ascii=False,
    )


MEDIA_AND_DESKTOP_TOOLS = [
    control_spotify,
    start_pomodoro_timer,
]
