"""Robust Spotify control through playerctl."""

import subprocess
from urllib.parse import quote


def _run_playerctl(args: list) -> tuple[bool, str]:
    """Run a playerctl command to control Spotify locally."""
    try:
        cmd = ["playerctl", "-p", "spotify"] + args
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True, res.stdout.strip()
    except Exception as e:
        return False, str(e)


def spotify_control(action: str, query: str = "", volume: int = 50) -> str:
    """Control Spotify music playback (works with Spotify Premium and Free through playerctl)."""

    if action == "pause":
        success, _ = _run_playerctl(["pause"])
        if success:
            return "⏸️ Musique mise en pause (via playerctl)."
        return "❌ Impossible de mettre en pause : vérifiez que Spotify est ouvert sur votre PC."

    elif action == "play":
        if query.strip():
            # No Spotify API key is configured in this project, so `query` cannot be resolved to a precise track
            # through the Spotify Web API. Instead we use the spotify:search:<query> URI scheme, sent to the local client
            # through OpenUri (MPRIS): Spotify opens directly on the search results.
            ok_open, _ = _run_playerctl(["open", f"spotify:search:{quote(query.strip())}"])
            if ok_open:
                return (
                    f"🔎 Spotify ouvert sur les résultats pour « {query.strip()} ». "
                    "Je ne peux pas lancer une piste précise sans API Spotify configurée : "
                    "dites-moi lequel des résultats lancer, ou donnez-moi directement un lien Spotify."
                )
            return "❌ Impossible d'ouvrir la recherche Spotify : vérifiez que Spotify est ouvert sur votre PC."

        success, _ = _run_playerctl(["play"])
        if success:
            return "▶️ Lecture Spotify lancée."
        return "❌ Impossible de relancer la lecture."

    elif action == "rewind":

        ok_pos, _ = _run_playerctl(["position", "0"])

        if not ok_pos:
            _run_playerctl(["previous"])

        _run_playerctl(["play"])
        return "⏮️ Morceau remis au début !"

    elif action == "next":
        _run_playerctl(["next"])
        return "⏭️ Morceau suivant."

    elif action == "previous":
        _run_playerctl(["previous"])
        return "⏮️ Morceau précédent."

    elif action == "current":
        ok_art, artist = _run_playerctl(["metadata", "artist"])
        ok_title, title = _run_playerctl(["metadata", "title"])
        if ok_art and ok_title:
            return f"🎵 En cours : '{title}' par {artist}"
        return "Aucune musique active sur Spotify."

    elif action == "volume":
        vol_float = max(0.0, min(1.0, volume / 100.0))
        ok, _ = _run_playerctl(["volume", str(vol_float)])
        if ok:
            return f"🔊 Volume réglé à {volume}%."
        return "❌ Impossible de régler le volume."

    return f"Action '{action}' non reconnue."
