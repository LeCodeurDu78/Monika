"""One conversation turn with a streaming speech pipeline; shared by local voice and Discord calls."""

from typing import Callable, Optional

ERROR_REPLY = "Je n'ai pas réussi à traiter ça, une erreur interne s'est produite. Tu peux reformuler ou réessayer."


def run_streamed_turn(
    messages: list,
    user_text: str,
    *,
    process: Callable[..., str],
    stream=None,
    on_wait: Optional[Callable[[str], None]] = None,
    **process_kwargs,
) -> str:
    """Run one agent turn and speak the reply through `stream`; return the reply.

    The history is rolled back if the turn raises, so it never keeps a tool call without its result.
    """
    snapshot = list(messages)
    messages.append({"role": "user", "content": user_text})
    try:
        reply = process(
            messages,
            on_wait=on_wait,
            on_text=stream.feed if stream is not None else None,
            **process_kwargs,
        )
    except Exception as exc:
        messages[:] = snapshot
        print(f"⚠️ [Erreur] {type(exc).__name__}: {exc}")
        reply = ERROR_REPLY

    if stream is not None:
        if reply and not stream.spoke_anything:
            stream.feed(reply)  # the server did not stream: still speak sentence by sentence
        stream.finish()
    return reply
