"""Sending Discord messages through the official REST API (bot), without a browser.

A Discord bot sends messages with plain HTTPS calls: no session to maintain, no QR code, no Playwright
thread. Settings (`.env`):
  DISCORD_BOT_TOKEN          bot token (Discord developer portal -> Bot -> Reset Token)
  DISCORD_DEFAULT_CHANNEL_ID default channel (optional), used when no recipient is given
"""

import re
import threading
import time

import requests

from core.settings import settings
from tools.social.contact_tools import find_contacts

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/monika, 1.0)"  # Discord requires this User-Agent format
MAX_MESSAGE_CHARS = 2000  # Discord limit per message
REQUEST_TIMEOUT_SECONDS = 15
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 10

_SNOWFLAKE = re.compile(r"^\d{15,22}$")
_CHANNEL_PREFIX = re.compile(r"^(?:channel|salon|#)\s*:?\s*(\d{15,22})$", re.IGNORECASE)

_session = requests.Session()
_dm_channels: dict[str, str] = {}  # user_id -> DM channel id (avoids an API call at every send)
_lock = threading.Lock()  # the scheduler and a conversation may send at the same time


class DiscordError(Exception):
    """Discord error with a message already readable by the model."""


def _explain(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = {}
    code = data.get("code")
    detail = data.get("message") or response.text[:200]
    if response.status_code == 401:
        return "jeton du bot invalide (vérifie DISCORD_BOT_TOKEN dans .env)."
    if code == 50007:
        return (
            "ce membre n'accepte pas les messages privés du bot. Il doit partager un serveur avec le bot "
            "et autoriser les MP des membres du serveur (Paramètres > Confidentialité)."
        )
    if code in (50001, 50013) or response.status_code == 403:
        return f"accès refusé ({detail}). Le bot doit être invité sur le serveur avec la permission « Envoyer des messages »."
    if code in (10003, 10013) or response.status_code == 404:
        return f"destinataire ou salon introuvable ({detail}). Vérifie l'identifiant Discord."
    return f"HTTP {response.status_code} : {detail}"


def _api(method: str, path: str, **kwargs) -> dict:
    """Call to the Discord API, handling the rate limit (429) and readable error messages."""
    token = settings.DISCORD_BOT_TOKEN
    if not token:
        raise DiscordError("DISCORD_BOT_TOKEN n'est pas configuré dans le fichier .env.")
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}

    for _ in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            response = _session.request(
                method, f"{API_BASE}{path}", headers=headers, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as exc:
            raise DiscordError(f"Discord injoignable ({type(exc).__name__}: {exc}).") from exc

        if response.status_code == 429:
            try:
                wait = float(response.json().get("retry_after", 1))
            except (ValueError, TypeError):
                wait = 1.0
            if wait > MAX_RATE_LIMIT_WAIT_SECONDS:
                raise DiscordError(f"limite de débit Discord atteinte, réessaie dans {wait:.0f} s.")
            time.sleep(wait + 0.1)
            continue
        if not response.ok:
            raise DiscordError(_explain(response))
        return response.json() if response.content else {}
    raise DiscordError("limite de débit Discord : trop de tentatives, réessaie dans un moment.")


def _split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Split a long text into messages <= limit, preferably at line breaks, then at spaces."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n ")
    if rest:
        chunks.append(rest)
    return [c for c in chunks if c]


def _dm_channel_id(user_id: str) -> str:
    if user_id not in _dm_channels:
        _dm_channels[user_id] = _api("POST", "/users/@me/channels", json={"recipient_id": user_id})["id"]
    return _dm_channels[user_id]


def _resolve(recipient: str) -> tuple[str, str, str]:
    """Return (channel_id, label, type): type = 'dm' (user ID to open) or 'channel'."""
    target = recipient.strip()
    if not target:
        default = settings.DISCORD_DEFAULT_CHANNEL_ID
        if not default:
            raise DiscordError("aucun destinataire donné et DISCORD_DEFAULT_CHANNEL_ID n'est pas défini.")
        return str(default), f"salon par défaut ({default})", "channel"

    match = _CHANNEL_PREFIX.match(target)
    if match:
        return match.group(1), f"salon {match.group(1)}", "channel"
    if _SNOWFLAKE.match(target):
        return target, f"utilisateur {target}", "dm"

    matches = find_contacts(target)
    if not matches:
        raise DiscordError(
            f"le contact '{target}' est introuvable. Ajoute-le avec manage_contacts "
            "(action='add', name, discord_id), ou donne directement l'identifiant numérique."
        )
    if len(matches) > 1:  # never send to whoever comes "first" when the name is ambiguous
        names = ", ".join(display for display, _ in matches)
        raise DiscordError(f"plusieurs contacts correspondent à '{target}' ({names}). Précise le nom complet.")
    display_name, discord_id = matches[0]
    return discord_id, display_name, "dm"


def send_discord_message(recipient: str = "", message: str = "") -> str:
    """Send a Discord message to a contact (direct message) or in a channel."""
    text = (message or "").strip()
    if not text:
        return "❌ Le message est vide."
    try:
        target_id, label, kind = _resolve(recipient or "")
        parts = _split_message(text)
        with _lock:
            channel_id = _dm_channel_id(target_id) if kind == "dm" else target_id
            for part in parts:
                # empty allowed_mentions: text coming from the web or an e-mail cannot trigger @everyone.
                _api(
                    "POST",
                    f"/channels/{channel_id}/messages",
                    json={"content": part, "allowed_mentions": {"parse": []}},
                )
        suffix = f" ({len(parts)} messages)" if len(parts) > 1 else ""
        return f"✅ Message Discord envoyé à {label}{suffix}."
    except DiscordError as exc:
        return f"❌ Échec de l'envoi Discord : {exc}"
