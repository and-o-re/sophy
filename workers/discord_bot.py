"""
Cliente e Ponte Real-Time do Discord para a Sophy (v2.1).
Conecta a Sophy de forma verdadeira ao servidor (DISCORD_GUILD_ID) e ao canal dedicado (DISCORD_CHANNEL_ID):
1. Mantém conexão WebSocket Gateway real via `discord.py` com presença Online e leitura de mensagens.
2. No canal dedicado (`DISCORD_CHANNEL_ID`, ex: `DISCORD_CHANNEL_ID` #bot), responde diretamente a todas
   as mensagens de usuários sem exigir menção (@Sophy), além de responder a menções, replies e DMs.
3. Enquanto o bot o bot da Sophy aguarda o clique de autorização OAuth2 para entrar como membro no servidor
   (ou caso o canal #bot seja restrito), opera uma Ponte Ao Vivo via Webhook batizado de "Sophy" no canal
   `DISCORD_CHANNEL_ID` para que ela já responda imediatamente no chat sem falhar!
"""

import asyncio
import os
import fcntl
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, Optional, Set

import discord
import httpx

from config import settings
from core.agent_core import LocalHybridAgent
from database.db_manager import db_manager, get_now_iso
from workers.sync_manager import _read_env_credentials

logger = logging.getLogger("sophy.discord")

LOCK_FILE_PATH = Path("/tmp/sophy_discord_gateway.lock")
PROCESSED_MSG_FILE = Path("/tmp/sophy_discord_processed_msgs.json")


def _load_processed_ids() -> Set[str]:
    try:
        if PROCESSED_MSG_FILE.exists():
            data = json.loads(PROCESSED_MSG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(str(x) for x in data[-500:])
    except Exception:
        pass
    return set()


def _save_processed_id(msg_id: str) -> None:
    try:
        ids = list(_load_processed_ids())
        if str(msg_id) not in ids:
            ids.append(str(msg_id))
        PROCESSED_MSG_FILE.write_text(json.dumps(ids[-500:]), encoding="utf-8")
    except Exception:
        pass


def _record_message_in_db(
    msg_id: str,
    channel_id: str,
    sender_name: str,
    content: str,
    is_mention: int = 0,
    is_replied: int = 0,
) -> None:
    """Persiste a mensagem do Discord no cache SQLite (`messages_cache`)."""
    now_str = get_now_iso()
    try:
        with db_manager.get_connection(read_only=False) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages_cache
                (external_id, platform, channel_or_chat, sender_name, content, is_mention, is_replied, sent_at, synced_at)
                VALUES (?, 'discord', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"disc-{msg_id}",
                    f"channel:{channel_id}",
                    sender_name,
                    content,
                    is_mention,
                    is_replied,
                    now_str,
                    now_str,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.debug(f"Falha ao gravar mensagem no SQLite: {exc}")


def _get_companion_bridge_token() -> str:
    """
    Opcional: lê um token de bot auxiliar de outro arquivo .env, indicado pela variável
    COMPANION_ENV_PATH (ex.: o .env da Sophya). Sem essa variável, não faz nada.
    """
    raw_path = os.getenv("COMPANION_ENV_PATH", "").strip()
    if not raw_path:
        return ""
    sophya_env = Path(raw_path).expanduser()
    if not sophya_env.exists():
        return ""
    try:
        for line in sophya_env.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("DISCORD_TOKEN="):
                return s.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


async def ensure_sophy_app_intents_and_install_params(bot_token: str) -> Optional[str]:
    """
    Garante na API do Discord que o aplicativo da Sophy tenha:
    - MESSAGE CONTENT, SERVER MEMBERS e PRESENCE Intents ativas (flags = 565248)
    - Escopos OAuth2 `bot` + `applications.commands` com permissão Administrator (8)
    Retorna o `application_id` se válido.
    """
    if not bot_token:
        return None
    headers = {"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            me_resp = await client.get("https://discord.com/api/v10/oauth2/applications/@me", headers=headers)
            if me_resp.status_code != 200:
                return None
            app_data = me_resp.json()
            app_id = str(app_data.get("id", ""))
            current_flags = int(app_data.get("flags") or 0)
            desired_flags = current_flags | (1 << 13) | (1 << 15) | (1 << 19)
            install_scopes = (app_data.get("install_params") or {}).get("scopes") or []

            if current_flags != desired_flags or "bot" not in install_scopes:
                await client.patch(
                    "https://discord.com/api/v10/applications/@me",
                    headers=headers,
                    json={
                        "flags": desired_flags,
                        "install_params": {
                            "scopes": ["bot", "applications.commands"],
                            "permissions": "8",
                        },
                        "integration_types_config": {
                            "0": {
                                "oauth2_install_params": {
                                    "scopes": ["bot", "applications.commands"],
                                    "permissions": "8",
                                }
                            },
                            "1": {
                                "oauth2_install_params": {
                                    "scopes": ["applications.commands"],
                                    "permissions": "0",
                                }
                            },
                        },
                    },
                )
            return app_id
    except Exception:
        return None


async def ensure_sophy_channel_permissions(guild_id: str, channel_id: str, sophy_bot_id: str) -> bool:
    """
    Quando a Sophy é membro do servidor `guild_id`, garante que ela tenha permissão
    explícita (`VIEW_CHANNEL | SEND_MESSAGES | READ_MESSAGE_HISTORY`) no canal privado `channel_id`.
    """
    companion_token = _get_companion_bridge_token()
    if not companion_token or not guild_id or not channel_id or not sophy_bot_id:
        return False

    headers = {"Authorization": f"Bot {companion_token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            member_check = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/members/{sophy_bot_id}",
                headers=headers,
            )
            if member_check.status_code != 200:
                return False

            # VIEW_CHANNEL (1024) | SEND_MESSAGES (2048) | EMBED_LINKS (16384) | ATTACH_FILES (32768) | READ_MESSAGE_HISTORY (65536)
            allow_bits = str(1024 | 2048 | 16384 | 32768 | 65536)
            perm_resp = await client.put(
                f"https://discord.com/api/v10/channels/{channel_id}/permissions/{sophy_bot_id}",
                headers=headers,
                json={"allow": allow_bits, "deny": "0", "type": 1},
            )
            return perm_resp.status_code in (200, 204)
    except Exception:
        return False


class SophyDiscordGatewayClient(discord.Client):
    """Cliente Discord Gateway (WebSocket) oficial da Sophy."""

    def __init__(self, service: "SophyDiscordService") -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.dm_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.service = service

    async def on_ready(self) -> None:
        activity = discord.Activity(
            type=discord.ActivityType.playing,
            name="estudando grimórios antigos ✨ | #bot",
        )
        await self.change_presence(status=discord.Status.online, activity=activity)
        if self.user:
            logger.info(f"Sophy conectada ao Gateway Discord como {self.user} ({self.user.id})")
            await ensure_sophy_channel_permissions(
                self.service.guild_id, self.service.channel_id, str(self.user.id)
            )
            await self.service.check_and_reply_unanswered_in_target_channel()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.user and str(guild.id) == str(self.service.guild_id):
            await ensure_sophy_channel_permissions(
                self.service.guild_id, self.service.channel_id, str(self.user.id)
            )
            await self.service.check_and_reply_unanswered_in_target_channel()

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.webhook_id is not None:
            return

        msg_id_str = str(message.id)
        if msg_id_str in self.service.processed_ids:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_target_channel = str(message.channel.id) == str(self.service.channel_id)
        is_mentioned = bool(self.user and self.user in message.mentions)
        is_reply_to_sophy = False

        if message.reference and message.reference.message_id:
            ref_msg = message.reference.resolved
            if isinstance(ref_msg, discord.Message) and self.user and ref_msg.author.id == self.user.id:
                is_reply_to_sophy = True

        if not (is_target_channel or is_mentioned or is_reply_to_sophy or is_dm):
            return

        self.service.mark_processed(msg_id_str)
        clean_content = message.content or ""
        if self.user:
            clean_content = re.sub(r"<@!?" + str(self.user.id) + r">", "", clean_content).strip()

        if not clean_content:
            clean_content = "Oi Sophy!"

        author_display = getattr(message.author, "display_name", None) or message.author.name
        _record_message_in_db(
            msg_id=msg_id_str,
            channel_id=str(message.channel.id),
            sender_name=author_display,
            content=clean_content,
            is_mention=1 if (is_mentioned or is_target_channel) else 0,
            is_replied=1,
        )

        async with message.channel.typing():
            reply_text = await self.service.generate_sophy_discord_reply(
                user_name=author_display,
                user_message=clean_content,
            )

        for chunk in _split_discord_message(reply_text):
            await message.reply(chunk, mention_author=False)


def _split_discord_message(text: str, max_len: int = 1900) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["... ＜(´⌯  ̫⌯`)＞"]
    if len(text) <= 2000:
        return [text]
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


class SophyDiscordService:
    """
    Serviço unificado do Discord da Sophy:
    - Mantém o cliente Gateway `SophyDiscordGatewayClient` conectado.
    - Se o bot o bot da Sophy ainda não estiver dentro do servidor `DISCORD_GUILD_ID`
      (ou não tiver acesso direto ao canal `DISCORD_CHANNEL_ID`), mantém uma ponte
      ativa no canal `DISCORD_CHANNEL_ID` via Webhook "Sophy" para que todas as
      mensagens enviadas lá sejam lidas e respondidas em tempo real pela Sophy!
    """

    def __init__(self) -> None:
        self.agent = LocalHybridAgent(tools=[])
        self.processed_ids: Set[str] = _load_processed_ids()
        self.client: Optional[SophyDiscordGatewayClient] = None
        self._gateway_task: Optional[asyncio.Task[None]] = None
        self._bridge_task: Optional[asyncio.Task[None]] = None
        self._lock_fd: Optional[int] = None
        self._reply_lock = asyncio.Lock()
        self._cached_webhook_url: Optional[str] = None

    @property
    def bot_token(self) -> str:
        creds = _read_env_credentials()
        return (creds.get("DISCORD_BOT_TOKEN") or settings.DISCORD_BOT_TOKEN).strip()

    @property
    def guild_id(self) -> str:
        creds = _read_env_credentials()
        return (creds.get("DISCORD_GUILD_ID") or settings.DISCORD_GUILD_ID ).strip()

    @property
    def channel_id(self) -> str:
        creds = _read_env_credentials()
        return (creds.get("DISCORD_CHANNEL_ID") or settings.DISCORD_CHANNEL_ID ).strip()

    def mark_processed(self, msg_id: str) -> None:
        self.processed_ids.add(str(msg_id))
        _save_processed_id(str(msg_id))

    def acquire_singleton_lock(self) -> bool:
        """Garante que apenas 1 processo local execute o listener de respostas do Discord."""
        if self._lock_fd is not None:
            return True
        try:
            fd = open(LOCK_FILE_PATH, "w", encoding="utf-8")
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = fd.fileno()
            self._lock_file_obj = fd
            return True
        except Exception:
            return False

    async def generate_sophy_discord_reply(self, user_name: str, user_message: str) -> str:
        """Gera a resposta da Sophy com a persona estilo Frieren para o chat do Discord."""
        async with self._reply_lock:
            prompt = (
                f"[Chat do Discord • Canal #bot • Mensagem de {user_name}]: {user_message}\n\n"
                "(Instrução interna: Responda diretamente ao usuário no Discord como a Sophy, "
                "com seu jeitinho calmo, sereno, contemplativo e fofo da Frieren ＜(´⌯  ̫⌯`)＞. "
                "NUNCA mencione RTX, placa de vídeo, GPU, VRAM, computador, diretórios ou hardware.)"
            )
            try:
                reply = await self.agent.chat(prompt)
                return reply.strip()
            except Exception as exc:
                logger.error(f"Erro ao gerar resposta da Sophy para o Discord: {exc}")
                return (
                    "Ah... me distraí um instante tentando ler um grimório difícil e perdi o fio da meada. "
                    "(=‐ω‐=) 📖 Pode repetir para mim?"
                )

    def is_native_bot_in_target_channel(self) -> bool:
        """Verifica se o cliente Gateway nativo o bot da Sophy já está no servidor e enxerga o canal alvo."""
        if not self.client or not self.client.is_ready():
            return False
        try:
            ch = self.client.get_channel(int(self.channel_id))
            return ch is not None
        except Exception:
            return False

    async def _get_or_create_sophy_webhook(self, http_client: httpx.AsyncClient, auth_token: str) -> Optional[str]:
        """Obtém ou cria um Webhook chamado 'Sophy' no canal alvo para envio imediato."""
        if self._cached_webhook_url:
            return self._cached_webhook_url

        headers = {"Authorization": f"Bot {auth_token}", "Content-Type": "application/json"}
        try:
            r = await http_client.get(
                f"https://discord.com/api/v10/channels/{self.channel_id}/webhooks",
                headers=headers,
            )
            if r.status_code == 200:
                for wh in r.json():
                    if wh.get("name") == "Sophy" and wh.get("token"):
                        self._cached_webhook_url = f"https://discord.com/api/v10/webhooks/{wh['id']}/{wh['token']}"
                        return self._cached_webhook_url

            create_r = await http_client.post(
                f"https://discord.com/api/v10/channels/{self.channel_id}/webhooks",
                headers=headers,
                json={"name": "Sophy"},
            )
            if create_r.status_code in (200, 201):
                wh = create_r.json()
                self._cached_webhook_url = f"https://discord.com/api/v10/webhooks/{wh['id']}/{wh['token']}"
                return self._cached_webhook_url
        except Exception as exc:
            logger.debug(f"Não foi possível criar/obter webhook Sophy: {exc}")
        return None

    async def check_and_reply_unanswered_in_target_channel(self) -> None:
        """
        Verifica as mensagens recentes no canal dedicado (`DISCORD_CHANNEL_ID`).
        Se a última mensagem do canal foi enviada por um humano (ex: 'teste 1' do usuário)
        e ainda não foi respondida pela Sophy, responde imediatamente!
        """
        if settings.STRICT_OFFLINE_MODE or not self.channel_id:
            return

        # 1. Se o bot nativo Sophy#4393 já tem acesso direto ao canal via Gateway:
        if self.is_native_bot_in_target_channel() and self.client:
            try:
                ch = self.client.get_channel(int(self.channel_id))
                if isinstance(ch, discord.TextChannel):
                    msgs = [m async for m in ch.history(limit=5)]
                    if msgs:
                        latest = msgs[0]
                        if (
                            not latest.author.bot
                            and latest.webhook_id is None
                            and str(latest.id) not in self.processed_ids
                        ):
                            self.mark_processed(str(latest.id))
                            author_name = getattr(latest.author, "display_name", None) or latest.author.name
                            content = (latest.content or "").strip() or "Oi Sophy!"
                            _record_message_in_db(
                                str(latest.id),
                                self.channel_id,
                                author_name,
                                content,
                                is_mention=1,
                                is_replied=1,
                            )
                            async with ch.typing():
                                reply = await self.generate_sophy_discord_reply(author_name, content)
                            for chunk in _split_discord_message(reply):
                                await latest.reply(chunk, mention_author=False)
                return
            except Exception as exc:
                logger.debug(f"Erro ao checar histórico via bot nativo: {exc}")

        # 2. Caso o bot Sophy#4393 ainda aguarde autorização no servidor ou permissão no canal #bot,
        # usa o token da ponte local + Webhook 'Sophy' para ler e responder no canal #bot!
        bridge_token = _get_companion_bridge_token()
        active_token = self.bot_token
        async with httpx.AsyncClient(timeout=8.0) as http_client:
            # Testa primeiro com o próprio token da Sophy
            headers = {"Authorization": f"Bot {active_token}"}
            resp = await http_client.get(
                f"https://discord.com/api/v10/channels/{self.channel_id}/messages?limit=5",
                headers=headers,
            )
            using_companion = False
            if resp.status_code != 200 and bridge_token:
                active_token = bridge_token
                headers = {"Authorization": f"Bot {active_token}"}
                resp = await http_client.get(
                    f"https://discord.com/api/v10/channels/{self.channel_id}/messages?limit=5",
                    headers=headers,
                )
                using_companion = True

            if resp.status_code != 200:
                return

            messages = resp.json()
            if not messages or not isinstance(messages, list):
                return

            # Sincroniza todas as mensagens lidas no SQLite
            for m in reversed(messages):
                m_id = str(m.get("id", ""))
                author_obj = m.get("author") or {}
                author_name = author_obj.get("global_name") or author_obj.get("username") or "unknown"
                _record_message_in_db(
                    m_id,
                    self.channel_id,
                    author_name,
                    m.get("content", ""),
                    is_mention=1 if m.get("mentions") else 0,
                    is_replied=0,
                )

            latest = messages[0]
            latest_id = str(latest.get("id", ""))
            latest_author = latest.get("author") or {}
            is_bot_or_wh = bool(latest_author.get("bot") or latest.get("webhook_id"))

            if is_bot_or_wh or latest_id in self.processed_ids:
                return

            # Marca como processada e gera a resposta da Sophy!
            self.mark_processed(latest_id)
            sender_display = (
                latest_author.get("global_name")
                or latest_author.get("username")
                or "usuário"
            )
            user_text = (latest.get("content") or "").strip()
            user_text = re.sub(r"<@!?\d+>", "", user_text).strip() or "Oi Sophy!"

            reply_text = await self.generate_sophy_discord_reply(sender_display, user_text)

            if not using_companion:
                # O próprio token da Sophy tem acesso REST ao canal
                for chunk in _split_discord_message(reply_text):
                    await http_client.post(
                        f"https://discord.com/api/v10/channels/{self.channel_id}/messages",
                        headers={"Authorization": f"Bot {self.bot_token}", "Content-Type": "application/json"},
                        json={
                            "content": chunk,
                            "message_reference": {"message_id": latest_id},
                            "allowed_mentions": {"replied_user": False},
                        },
                    )
            else:
                # Envia no canal #bot através do Webhook dedicado da Sophy!
                wh_url = await self._get_or_create_sophy_webhook(http_client, active_token)
                if wh_url:
                    for chunk in _split_discord_message(reply_text):
                        sent = await http_client.post(
                            f"{wh_url}?wait=true",
                            json={
                                "username": "Sophy",
                                "content": chunk,
                            },
                        )
                        if sent.status_code in (200, 201):
                            s_json = sent.json()
                            _record_message_in_db(
                                str(s_json.get("id", "")),
                                self.channel_id,
                                "Sophy",
                                chunk,
                                is_mention=0,
                                is_replied=1,
                            )

    async def _channel_bridge_poll_loop(self) -> None:
        """
        Loop leve que garante que o canal dedicado (`DISCORD_CHANNEL_ID`) seja monitorado
        mesmo enquanto o bot o bot da Sophy aguarda o clique no convite OAuth2 (ou reconexão).
        Assim que o bot nativo o bot da Sophy entra no servidor, também garante as permissões
        do canal privado `#bot` automaticamente.
        """
        sophy_app_id = await ensure_sophy_app_intents_and_install_params(self.bot_token)
        while True:
            try:
                if not settings.STRICT_OFFLINE_MODE and self.bot_token:
                    if sophy_app_id and not self.is_native_bot_in_target_channel():
                        await ensure_sophy_channel_permissions(
                            self.guild_id, self.channel_id, sophy_app_id
                        )
                    await self.check_and_reply_unanswered_in_target_channel()
            except Exception as exc:
                logger.debug(f"Bridge poll loop info: {exc}")
            await asyncio.sleep(2.5)

    async def _gateway_runner(self) -> None:
        """Mantém a conexão WebSocket Gateway do `discord.py` ativa com reconexão automática."""
        while True:
            if settings.STRICT_OFFLINE_MODE or not self.bot_token:
                await asyncio.sleep(5.0)
                continue
            try:
                await ensure_sophy_app_intents_and_install_params(self.bot_token)
                self.client = SophyDiscordGatewayClient(self)
                await self.client.start(self.bot_token)
            except Exception as exc:
                logger.warning(f"Gateway Discord Sophy reconectando em 5s: {exc}")
            finally:
                try:
                    if self.client and not self.client.is_closed():
                        await self.client.close()
                except Exception:
                    pass
            await asyncio.sleep(5.0)

    def start_background(self) -> bool:
        """Inicia o cliente Gateway e o monitor do canal em background (singleton na máquina)."""
        if not self.acquire_singleton_lock():
            return False

        if self._gateway_task is None or self._gateway_task.done():
            self._gateway_task = asyncio.create_task(self._gateway_runner())
        if self._bridge_task is None or self._bridge_task.done():
            self._bridge_task = asyncio.create_task(self._channel_bridge_poll_loop())
        return True


sophy_discord_service = SophyDiscordService()


async def get_discord_live_status() -> Dict[str, Any]:
    """
    Retorna um diagnóstico verdadeiro e detalhado da conexão da Sophy com o Discord:
    - Se o token é válido e qual o usuário/ID do bot (o bot da Sophy).
    - Se o bot o bot da Sophy já está como membro no servidor `DISCORD_GUILD_ID` (`DISCORD_GUILD_ID`).
    - Se o canal `DISCORD_CHANNEL_ID` (`DISCORD_CHANNEL_ID` #bot) está acessível diretamente ou operando
      via Ponte Webhook Ativa da Sophy.
    - URL oficial de convite OAuth2 caso o usuário queira autorizar o usuário bot o bot da Sophy no servidor.
    """
    creds = _read_env_credentials()
    bot_token = (creds.get("DISCORD_BOT_TOKEN") or settings.DISCORD_BOT_TOKEN).strip()
    guild_id = (creds.get("DISCORD_GUILD_ID") or settings.DISCORD_GUILD_ID ).strip()
    channel_id = (creds.get("DISCORD_CHANNEL_ID") or settings.DISCORD_CHANNEL_ID ).strip()

    if settings.STRICT_OFFLINE_MODE:
        return {
            "configured": bool(bot_token),
            "online": False,
            "mode": "OFFLINE_SEALED",
            "guild_id": guild_id,
            "channel_id": channel_id,
        }

    if not bot_token:
        return {
            "configured": False,
            "online": False,
            "mode": "UNCONFIGURED",
            "guild_id": guild_id,
            "channel_id": channel_id,
        }

    headers = {"Authorization": f"Bot {bot_token}"}
    companion_token = _get_companion_bridge_token()

    try:
        async with httpx.AsyncClient(timeout=3.5) as client:
            me_resp = await client.get("https://discord.com/api/v10/users/@me", headers=headers)
            if me_resp.status_code != 200:
                return {
                    "configured": True,
                    "online": False,
                    "mode": "INVALID_TOKEN",
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                }

            me_data = me_resp.json()
            bot_id = str(me_data.get("id", ""))
            bot_username = me_data.get("username", "Sophy")
            invite_url = (
                f"https://discord.com/oauth2/authorize?client_id={bot_id}"
                f"&permissions=8&scope=bot%20applications.commands"
                f"&guild_id={guild_id}&disable_guild_select=true"
            )

            ch_resp = await client.get(
                f"https://discord.com/api/v10/channels/{channel_id}",
                headers=headers,
            )
            if ch_resp.status_code == 200:
                ch_name = ch_resp.json().get("name", "bot")
                return {
                    "configured": True,
                    "online": True,
                    "in_guild": True,
                    "channel_accessible": True,
                    "mode": "NATIVE_GATEWAY",
                    "bot_id": bot_id,
                    "bot_username": bot_username,
                    "guild_id": guild_id,
                    "channel_id": channel_id,
                    "channel_name": ch_name,
                    "invite_url": invite_url,
                }

            # Verifica se a Ponte Webhook no canal #bot está ativa
            if companion_token:
                comp_ch = await client.get(
                    f"https://discord.com/api/v10/channels/{channel_id}",
                    headers={"Authorization": f"Bot {companion_token}"},
                )
                if comp_ch.status_code == 200:
                    ch_name = comp_ch.json().get("name", "bot")
                    return {
                        "configured": True,
                        "online": True,
                        "in_guild": False,
                        "channel_accessible": True,
                        "mode": "WEBHOOK_BRIDGE_ACTIVE",
                        "bot_id": bot_id,
                        "bot_username": bot_username,
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                        "channel_name": ch_name,
                        "invite_url": invite_url,
                    }

            return {
                "configured": True,
                "online": True,
                "in_guild": False,
                "channel_accessible": False,
                "mode": "AWAITING_GUILD_INVITE",
                "bot_id": bot_id,
                "bot_username": bot_username,
                "guild_id": guild_id,
                "channel_id": channel_id,
                "invite_url": invite_url,
            }
    except Exception as exc:
        return {
            "configured": True,
            "online": False,
            "mode": "NETWORK_ERROR",
            "error": str(exc),
            "guild_id": guild_id,
            "channel_id": channel_id,
        }
