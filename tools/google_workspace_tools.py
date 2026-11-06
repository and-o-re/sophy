"""
Ferramentas do Google Workspace (`andmarios/google-workspace-skill` / `uvx gws-cli`).
Integra Gmail, Google Calendar, Google Drive, Google Docs, Google Sheets, Google Contacts e Google Tasks
via `uvx gws-cli` e OAuth2 (`~/.config/gws-cli/client_secret.json`).
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List

from config import BASE_DIR, settings


def ensure_gws_client_secret_file() -> Path:
    """
    Garante que o diretório `~/.config/gws-cli/` exista e, caso o usuário tenha preenchido
    `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` no Painel Desktop da Sophy, gera automaticamente
    o arquivo `~/.config/gws-cli/client_secret.json` no formato Desktop OAuth2 esperado pelo `gws-cli`.
    """
    secret_path = Path(settings.GOOGLE_WORKSPACE_CLIENT_SECRET_JSON).expanduser()
    secret_path.parent.mkdir(parents=True, exist_ok=True)

    client_id = settings.GOOGLE_CLIENT_ID.strip()
    client_secret = settings.GOOGLE_CLIENT_SECRET.strip()

    if client_id and client_secret and not secret_path.exists() and not (secret_path.parent / "client_secret.json.enc").exists():
        payload = {
            "installed": {
                "client_id": client_id,
                "project_id": "sophy-local-hybrid-ai",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": client_secret,
                "redirect_uris": ["http://localhost"],
            }
        }
        secret_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return secret_path


def get_gws_auth_status() -> Dict[str, Any]:
    """Verifica se `client_secret.json` (ou `.enc`) e o token OAuth2 do `gws-cli` estão configurados."""
    secret_path = ensure_gws_client_secret_file()
    cfg_dir = secret_path.parent
    secret_enc = cfg_dir / "client_secret.json.enc"
    token_files = list(cfg_dir.glob("*token*")) + list(cfg_dir.glob("credentials*"))
    gws_bin = shutil.which("gws-cli") or str(Path.home() / ".local" / "bin" / "gws-cli")
    uvx_bin = shutil.which("uvx") or str(BASE_DIR / ".venv" / "bin" / "uvx")
    return {
        "client_secret_path": str(secret_enc if secret_enc.exists() else secret_path),
        "client_secret_exists": secret_path.exists() or secret_enc.exists(),
        "oauth_token_exists": len(token_files) > 0 or bool(settings.GOOGLE_ACCESS_TOKEN.strip()),
        "gws_binary": gws_bin,
        "uvx_binary": uvx_bin,
        "auth_command": f"{gws_bin} auth" if Path(gws_bin).exists() else f"{uvx_bin} gws-cli auth",
    }


def gws_run_command(subcommand_args: str) -> str:
    """
    [Skill: google-workspace-skill] Executa qualquer comando do `gws-cli` (`gws-cli <args>`)
    para operar Gmail, Calendar, Drive, Contacts, Docs, Sheets e Slides com saída JSON estruturada.
    Exemplos de `subcommand_args`:
    - 'gmail list --query "is:unread" --max 10'
    - 'calendar list'
    - 'drive list --max 10'
    """
    if settings.STRICT_OFFLINE_MODE:
        return json.dumps(
            {"status": "BLOCKED_OFFLINE", "message": "Modo Estritamente Offline ativo."},
            ensure_ascii=False,
        )

    status = get_gws_auth_status()
    if not status["client_secret_exists"]:
        return json.dumps(
            {
                "status": "MISSING_CREDENTIALS",
                "message": (
                    "Para usar o Google Workspace (`gws-cli`), insira `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET` "
                    "na Aba 03 do Painel Desktop (ou salve `~/.config/gws-cli/client_secret.json`) e rode "
                    f"`{status['auth_command']}`."
                ),
                "auth_status": status,
            },
            ensure_ascii=False,
            indent=2,
        )

    import shlex
    gws_bin = status.get("gws_binary") or ""
    if Path(gws_bin).exists():
        cmd = [gws_bin, *shlex.split(subcommand_args)]
    else:
        cmd = [status["uvx_binary"], "gws-cli", *shlex.split(subcommand_args)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return json.dumps(
            {
                "status": "OK" if proc.returncode == 0 else "CLI_ERROR",
                "command": " ".join(cmd),
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False)


def gws_gmail_search_and_read(query: str = "is:unread", max_results: int = 10) -> str:
    """
    [Skill: google-workspace-skill] Pesquisa e lista e-mails do Gmail usando a sintaxe de busca do Gmail
    (ex: 'from:alguem is:unread', 'subject:fatura').
    """
    if query.strip():
        return gws_run_command(f'gmail list --query "{query.strip()}" --max {int(max_results)}')
    return gws_run_command(f"gmail list --max {int(max_results)}")


def gws_gmail_send_or_draft(
    to_email: str,
    subject: str,
    body_html_or_text: str,
    as_draft: bool = False,
) -> str:
    """
    [Skill: google-workspace-skill] Envia um e-mail ou cria um rascunho no Gmail em formato HTML/texto.
    Parâmetros:
    - to_email: Destinatário.
    - subject: Assunto.
    - body_html_or_text: Corpo da mensagem.
    - as_draft: Se True, salva como rascunho (`gmail create-draft`) em vez de enviar imediatamente.
    """
    sub = "gmail create-draft" if as_draft else "gmail send"
    safe_sub = subject.replace('"', '\\"')
    safe_body = body_html_or_text.replace('"', '\\"')
    return gws_run_command(f'{sub} --to "{to_email}" --subject "{safe_sub}" --body "{safe_body}"')


def gws_calendar_events(action: str = "list", summary: str = "", start_iso: str = "", end_iso: str = "") -> str:
    """
    [Skill: google-workspace-skill] Lista ou cria eventos na agenda do Google Calendar.
    Parâmetros:
    - action: 'list' ou 'create'.
    - summary: Título do evento (para 'create').
    - start_iso: Data/hora inicial ISO 8601 (ex: '2026-11-03T14:00:00-03:00').
    - end_iso: Data/hora final ISO 8601.
    """
    if action == "create" and summary and start_iso and end_iso:
        return gws_run_command(
            f'calendar create --summary "{summary}" --start "{start_iso}" --end "{end_iso}"'
        )
    args = "calendar list"
    if start_iso:
        args += f' --from "{start_iso}"'
    if end_iso:
        args += f' --to "{end_iso}"'
    return gws_run_command(args)


def gws_drive_search_and_manage(query: str = "", max_results: int = 15) -> str:
    """
    [Skill: google-workspace-skill] Pesquisa arquivos e pastas no Google Drive.
    """
    if query.strip():
        return gws_run_command(f'drive search "{query.strip()}" --max {int(max_results)}')
    return gws_run_command(f"drive list --max {int(max_results)}")


def gws_docs_create_from_markdown(title: str, markdown_file_path: str) -> str:
    """
    [Skill: google-workspace-skill] Cria um documento formatado no Google Docs a partir de uma nota Markdown local.
    """
    return gws_run_command(f'docs create "{title}" --from "{markdown_file_path}"')


def gws_sheets_read_write(spreadsheet_id: str, range_a1: str = "A1:Z50") -> str:
    """
    [Skill: google-workspace-skill] Lê intervalos de células de uma planilha do Google Sheets.
    """
    return gws_run_command(f'sheets read "{spreadsheet_id}" "{range_a1}"')


GOOGLE_WORKSPACE_TOOLS = [
    gws_run_command,
    gws_gmail_search_and_read,
    gws_gmail_send_or_draft,
    gws_calendar_events,
    gws_drive_search_and_manage,
    gws_docs_create_from_markdown,
    gws_sheets_read_write,
]
