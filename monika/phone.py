"""Accès depuis le téléphone : le nom Tailscale de cette machine et le lien HTTPS à ouvrir.

`tailscale serve` ne proxyfie que `http://127.0.0.1` : Monika reste donc sur 127.0.0.1 (`WEB_HOST`)
et c'est Tailscale qui termine le HTTPS. Le jeton (`WEB_TOKEN`) ne change pas : il passe une fois
dans l'URL puis vit en cookie (voir `web/server.py`).
"""

from __future__ import annotations

import json
import shutil
import subprocess

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
TAILSCALE_TIMEOUT_S = 5
SERVE_HTTPS_PORT = 443  # port HTTPS par défaut de `tailscale serve`


class PhoneError(Exception):
    """L'accès depuis le téléphone ne peut pas être préparé ; le message est en français."""


def dns_name(status_json: str) -> str:
    """Le nom MagicDNS de cette machine (`archlinux.tail216c90.ts.net`), sans le point final."""
    try:
        data = json.loads(status_json)
    except ValueError as exc:
        raise PhoneError(f"État de Tailscale illisible ({exc}).") from exc
    name = str((data.get("Self") or {}).get("DNSName") or "").strip().rstrip(".")
    if not name:
        raise PhoneError(
            "Tailscale ne donne pas de nom MagicDNS : active MagicDNS et les certificats HTTPS "
            "dans la console Tailscale."
        )
    return name


def serve_link(name: str, token: str, https_port: int = SERVE_HTTPS_PORT) -> str:
    """Le lien à ouvrir une fois sur le téléphone (le jeton devient un cookie, l'adresse reste propre)."""
    suffix = "" if https_port == SERVE_HTTPS_PORT else f":{https_port}"
    base = f"https://{name}{suffix}/"
    return f"{base}?token={token}" if token else base


def tailscale_name() -> str:
    """Le nom MagicDNS de cette machine, lu sur `tailscale status` (jamais d'état modifié)."""
    if shutil.which("tailscale") is None:
        raise PhoneError(
            "Tailscale n'est pas installé sur cette machine : installe le paquet tailscale "
            "puis connecte-la (`tailscale up`)."
        )
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=TAILSCALE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PhoneError(f"Tailscale ne répond pas ({exc}).") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = f" ({detail[0]})" if detail else ""
        raise PhoneError(f"Tailscale n'est pas connecté{reason} : lance `tailscale up`.")
    return dns_name(result.stdout)
