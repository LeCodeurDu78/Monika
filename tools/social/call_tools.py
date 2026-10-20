"""Tool that lets Monika start or end a Discord voice call (see discord/discord_voice.py)."""


def discord_call(action: str, message: str = "") -> str:
    """Call the user on Discord voice ('call_me'), hang up ('hangup'), or report the call state ('status')."""
    from discord.discord_voice import get_manager

    manager = get_manager()
    if manager is None:
        return "❌ Les appels Discord ne sont pas disponibles (DISCORD_CALLS_ENABLED ou écoute Discord désactivée)."
    try:
        return manager.run(action, message)
    except Exception as exc:
        return f"❌ Appel impossible : {type(exc).__name__}: {exc}"
