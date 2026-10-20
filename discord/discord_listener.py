"""Discord listener: Monika answers the messages her authorised users send her.

How it works: a background thread runs the Discord client (discord.py, "gateway" connection). Every accepted
message triggers an agent turn (process_user_message) with one history PER channel, then the reply is sent
back to Discord, split into messages of at most 2000 characters.

Attachments: images, PDF, DOCX, text and audio are read before the agent turn (see
discord/discord_attachments.py). When a slow tool runs, a short waiting phrase is posted in the channel
(tool flags `lent` / `phrase_attente`, see tools/registry.py).

Voice calls: with DISCORD_CALLS_ENABLED, "/appel" makes Monika join your voice channel (see
discord/discord_voice.py).

SECURITY: Monika can run commands and read files. Only the IDs in DISCORD_ALLOWED_USER_IDS are listened to;
all other messages are ignored. Accepted:
  - direct messages (DMs) from an authorised user;
  - in a server, their messages that @mention the bot, or that are in DISCORD_LISTEN_CHANNEL_IDS.
"""

import asyncio
import re
import threading
from typing import Callable, Optional

import discord

from core.settings import settings
from discord.discord_attachments import Attachment, build_message
from tools.social.discord_tools import _split_message, send_discord_message

RESET_COMMANDS = {"/reset", "!reset", "reset", "/nouveau", "!nouveau"}
CALL_COMMANDS = {"/appel", "!appel", "/call", "!call"}
HANGUP_COMMANDS = {"/raccroche", "!raccroche", "/hangup", "!hangup"}
_NO_MENTIONS = discord.AllowedMentions.none()  # a reply must never ping @everyone or anyone
RETRY_MIN_SECONDS = 5  # wait before retrying a failed connection (doubled at each failure)
RETRY_MAX_SECONDS = 60
_ERROR_REPLY ="Je n'ai pas réussi à traiter ça, une erreur interne s'est produite. Tu peux reformuler ou réessayer."


def parse_ids(raw: Optional[str]) -> list[int]:
    """Discord IDs from a free-form string (commas, spaces, quotes...), without duplicates, in order."""
    seen: list[int] = []
    for token in re.findall(r"\d{15,22}", raw or ""):
        if int(token) not in seen:
            seen.append(int(token))
    return seen


class _Client(discord.Client):
    def __init__(self, listener: "DiscordListener", intents: discord.Intents) -> None:
        super().__init__(intents=intents)
        self._listener = listener

    async def on_ready(self) -> None:
        print(f"✅ [Discord] Écoute active en tant que {self.user} ({len(self._listener.allowed)} utilisateur(s) autorisé(s)).")
        if self._listener.calls is not None:
            self._listener.calls.scan()

    async def on_voice_state_update(self, member, before, after) -> None:
        if self._listener.calls is None:
            return
        try:
            await self._listener.calls.on_voice_state_update(member, before, after)
        except Exception as exc:  # a call problem must not stop the text listener
            print(f"⚠️ [Discord] Voice state error: {type(exc).__name__}: {exc}")

    async def on_message(self, message: discord.Message) -> None:
        try:
            await self._listener.handle_message(message, self.user)
        except Exception as exc:  # an error on one message must not stop listening
            print(f"⚠️ [Discord] Erreur sur un message : {type(exc).__name__}: {exc}")


class DiscordListener:
    def __init__(self, process: Callable[..., str]) -> None:
        """`process(messages, **kwargs)` runs an agent turn on the message list and returns the reply."""
        self._process = process
        self.allowed: list[int] = parse_ids(settings.DISCORD_ALLOWED_USER_IDS)
        self.channels: set[int] = set(parse_ids(settings.DISCORD_LISTEN_CHANNEL_IDS))
        self._histories: dict[int, list] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[discord.Client] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = False
        self.calls = None  # CallManager, created in _main when DISCORD_CALLS_ENABLED

    # --- cycle de vie ------------------------------------------------------------------
    def start(self) -> bool:
        if not settings.DISCORD_BOT_TOKEN:
            print("⚠️ [Discord] Écoute désactivée : DISCORD_BOT_TOKEN n'est pas défini dans .env.")
            return False
        if not self.allowed:
            print(
                "⚠️ [Discord] Écoute désactivée : DISCORD_ALLOWED_USER_IDS est vide. Sans liste d'utilisateurs "
                "autorisés, n'importe qui pourrait piloter ton ordinateur ; ajoute ton identifiant Discord."
            )
            return False
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="discord-listener")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stopping = True
        loop, client = self._loop, self._client
        if self.calls is not None and loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.calls.hangup(), loop).result(timeout=5)
            except Exception:
                pass
        if loop is not None and client is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=5)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            print(f"⚠️ [Discord] Écoute arrêtée : {type(exc).__name__}: {exc}")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        intents = discord.Intents.default()
        intents.message_content = settings.DISCORD_MESSAGE_CONTENT_INTENT
        intents.voice_states = True  # who is in which voice channel (needed to join and call)
        if settings.DISCORD_CALLS_ENABLED and self.calls is None:
            from discord.discord_voice import CallManager, set_manager

            self.calls = CallManager(self)
            set_manager(self.calls)
        delay = RETRY_MIN_SECONDS
        while not self._stopping:
            # A closed client cannot be restarted: create a new one at each attempt.
            self._client = _Client(self, intents)
            try:
                # After a first successful connection, discord.py reconnects by itself (reconnect=True).
                await self._client.start(settings.DISCORD_BOT_TOKEN)
            except discord.LoginFailure:
                print("❌ [Discord] Jeton invalide : vérifie DISCORD_BOT_TOKEN dans .env.")
                return
            except discord.PrivilegedIntentsRequired:
                print(
                    "❌ [Discord] Intent privilégié refusé : active « Message Content Intent » dans le portail "
                    "développeur (onglet Bot), ou mets DISCORD_MESSAGE_CONTENT_INTENT=false."
                )
                return
            except Exception as exc:  # no network at startup, Discord outage...
                if self._stopping:
                    return
                print(f"⚠️ [Discord] Connexion impossible ({type(exc).__name__}) : nouvelle tentative dans {delay:.0f} s.")
            finally:
                await self._client.close()
            for _ in range(int(delay)):  # wait split into small steps so that stop() is noticed quickly
                if self._stopping:
                    return
                await asyncio.sleep(1)
            delay = min(delay * 2, RETRY_MAX_SECONDS)

    def notify_owner(self, text: str) -> None:
        """Send a DM to the first authorised user (e.g. the result of a scheduled task)."""
        if not self.allowed:
            return
        result = send_discord_message(str(self.allowed[0]), text)
        if result.startswith("❌"):
            print(f"⚠️ [Discord] Notification non envoyée : {result}")

    # --- handling a message ---------------------------------------------------------
    async def handle_message(self, message, bot_user) -> bool:
        """Handle a Discord message. Return True if it was accepted and handled."""
        author = message.author
        if author.bot or author.id == bot_user.id:
            return False

        is_dm = message.guild is None
        mentioned = any(user.id == bot_user.id for user in message.mentions)
        if not is_dm and not (mentioned or message.channel.id in self.channels):
            return False  # channel message that does not concern us
        if author.id not in self.allowed:
            print(f"🚫 [Discord] Message ignoré : utilisateur non autorisé ({author.id}).")
            return False

        text = re.sub(rf"<@!?{bot_user.id}>", "", message.content or "").strip()
        attachments = list(message.attachments)
        if not text and not attachments:
            return False

        if self.calls is not None and not attachments:
            command = text.lower()
            if command in CALL_COMMANDS or command in HANGUP_COMMANDS:
                reply = (
                    await self.calls.join_user(author.id)
                    if command in CALL_COMMANDS
                    else await self.calls.hangup()
                )
                await message.channel.send(reply, allowed_mentions=_NO_MENTIONS)
                return True

        key = message.channel.id
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # one message at a time per channel, in order of arrival
            if text.lower() in RESET_COMMANDS:
                self._histories.pop(key, None)
                await message.channel.send("🧹 Conversation réinitialisée.", allowed_mentions=_NO_MENTIONS)
                return True

            loop = asyncio.get_running_loop()
            async with message.channel.typing():
                if attachments:
                    text = await self._with_attachments(text, attachments, loop)
                print(f"💬 [Discord] {author}: {text[:300]}{'…' if len(text) > 300 else ''}")
                on_wait = self._wait_notifier(message.channel, loop)
                reply = await loop.run_in_executor(None, self._turn, key, text, on_wait)
            print(f"💬 [Discord] Monika: {reply[:300]}{'…' if len(reply) > 300 else ''}")
            for chunk in _split_message(reply) or ["…"]:
                await message.channel.send(chunk, allowed_mentions=_NO_MENTIONS)
        return True

    # --- attachments ------------------------------------------------------------------
    async def _download_attachments(self, attachments) -> tuple[list[Attachment], list[str]]:
        """Download the attachments within the configured limits; return (kept, reasons for the skipped ones)."""
        max_files = settings.DISCORD_ATTACHMENT_MAX_FILES
        max_mb = settings.DISCORD_ATTACHMENT_MAX_MB
        max_bytes = int(max_mb * 1024 * 1024)
        kept: list[Attachment] = []
        skipped: list[str] = []
        for att in attachments:
            if len(kept) >= max_files:
                skipped.append(f"{att.filename} (au plus {max_files} fichiers lus par message)")
                continue
            if att.size > max_bytes:
                skipped.append(f"{att.filename} ({att.size / (1024 * 1024):.1f} Mo, au-dessus de la limite de {max_mb:g} Mo)")
                continue
            try:
                data = await att.read()
            except Exception as exc:
                skipped.append(f"{att.filename} (téléchargement impossible : {type(exc).__name__})")
                continue
            kept.append(Attachment(att.filename, data, att.content_type or ""))
        return kept, skipped

    async def _with_attachments(self, text: str, attachments, loop: asyncio.AbstractEventLoop) -> str:
        """The user's text completed with the content of their attachments (vision, PDF, Whisper...)."""
        if not settings.DISCORD_ATTACHMENTS_ENABLED:
            note = f"[{len(attachments)} pièce(s) jointe(s) reçue(s) : lecture des fichiers désactivée (DISCORD_ATTACHMENTS_ENABLED=false)]"
            return f"{text}\n{note}".strip()
        kept, skipped = await self._download_attachments(attachments)
        # Vision, PDF extraction and Whisper are blocking: keep them out of the asyncio loop.
        return await loop.run_in_executor(None, build_message, text, kept, skipped)

    # --- phrase d'attente ----------------------------------------------------------------
    def _wait_notifier(self, channel, loop: asyncio.AbstractEventLoop):
        """`on_wait(phrase)` function that posts the waiting phrase in the channel (called from another thread)."""

        def notify(phrase: str) -> None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    channel.send(phrase, allowed_mentions=_NO_MENTIONS), loop
                )
                future.result(timeout=10)
            except Exception as exc:
                print(f"⚠️ [Discord] Phrase d'attente non envoyée : {type(exc).__name__}: {exc}")

        return notify

    def _turn(self, key: int, text: str, on_wait: Optional[Callable[[str], None]] = None) -> str:
        """One agent turn (blocking: called outside the asyncio loop)."""
        messages = self._histories.setdefault(key, [])
        snapshot = list(messages)  # if the turn fails, go back to a consistent history
        messages.append({"role": "user", "content": text})
        try:
            return self._process(messages, on_wait=on_wait) or "Action terminée sans message retourné."
        except Exception as exc:
            messages[:] = snapshot
            print(f"⚠️ [Discord] Erreur de traitement : {type(exc).__name__}: {exc}")
            return _ERROR_REPLY
