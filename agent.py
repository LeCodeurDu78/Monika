"""Main conversation loop of Monika: terminal and voice sessions, plus the background watchers."""

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from web import state as avatar_state
from web.server import start_avatar_server
from config import SYSTEM_PROMPT, EXIT_WORDS
from agents.orchestrator import DISCORD_CONTEXT, SCHEDULED_TASK_CONTEXT, process_user_message
from agents.turn import run_streamed_turn
from core import briefing
from core.background_jobs import background_jobs
from core.settings import settings
from core.watcher import start_watcher
from memory.manager import memory_manager
from tools.utils.scheduler_tools import pop_due_tasks
from voice.voice_tts import speak

_EXIT_PHRASES = tuple(word for word in EXIT_WORDS if " " in word)
_EXIT_PHRASE_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(phrase) for phrase in _EXIT_PHRASES) + r")\b"
) if _EXIT_PHRASES else None


def _is_exit(user_text: str) -> bool:
    """True if the user explicitly asks to end the session.

    A single word such as "stop" must be the whole message: matching it as a substring used to make Monika
    quit on "stoppe la musique". Multi-word phrases ("au revoir", "quitte monika") are recognised inside a
    sentence, but only on word boundaries.
    """
    normalized = user_text.strip().lower().strip(" .!?…,;:")
    if normalized in EXIT_WORDS:
        return True
    return bool(_EXIT_PHRASE_PATTERN and _EXIT_PHRASE_PATTERN.search(normalized))


@dataclass
class Channel:
    """Adapts the session to an I/O mode: where input comes from and how replies are delivered."""

    get_input: Optional[Callable[[], Optional[str]]] = None
    speak_replies: bool = False
    # True for channels with no avatar updates of their own. Voice mode drives idle/listening/thinking/speaking
    # from the real audio (voice/voice_audio.py, avatar/lipsync.py); text mode has nothing else to animate the avatar.
    drives_avatar: bool = False
    # Voice only: build a streaming speech pipeline for each turn (None = speak the whole reply at the end).
    open_stream: Optional[Callable[[], object]] = None
    stream: Optional[object] = None  # speech pipeline of the turn in progress
    _speech_lock: threading.Lock = field(default_factory=threading.Lock)

    def _maybe_speak(self, text: str) -> None:
        if not self.speak_replies:
            return
        with self._speech_lock:
            speak(text)

    def say_waiting(self, text: str) -> None:
        """Waiting phrase while a slow tool runs (see the `lent` flag in tools/registry.py).

        Called from another thread than the session loop, so the avatar state is left alone. During a turn
        with a speech pipeline the phrase joins it, which keeps the order with the streamed text.
        """
        print(f"\n⏳ Monika: {text}")
        stream = self.stream
        if stream is not None:
            stream.say(text)
        else:
            self._maybe_speak(text)

    def deliver(self, text: str, icon: Optional[str] = None, already_spoken: bool = False) -> None:
        """Show a message from Monika (and speak it in voice mode unless it already was)."""
        prefix = f"{icon} " if icon else ""
        print(f"\n{prefix}Monika: {text}")
        if self.drives_avatar:
            avatar_state.set_state("speaking", text=text)
        if not already_spoken:
            self._maybe_speak(text)
        if self.drives_avatar:
            if not self.speak_replies:
                # Without audio, "speaking" would last only as long as a print(), far too short for the
                # front end's 100 ms polling (POLL_INTERVAL_MS in script.js) to ever see it. Keep it for a
                # time proportional to the text, capped so a very long reply does not drag on.
                time.sleep(max(0.6, min(4.0, len(text) / 18)))
            avatar_state.set_state("idle")


def _read_text_input() -> Optional[str]:
    """Read one line from the keyboard."""
    try:
        text = input("\nToi: ")
    except EOFError:
        return None
    return None if _is_exit(text) else text


def _read_voice_input() -> Optional[str]:
    """Record from the microphone until the user stops talking, then transcribe."""
    from voice.voice_audio import record_until_silence
    from voice.voice_stt import transcribe

    audio = record_until_silence()
    if audio is None or audio.size == 0:
        return ""
    text = transcribe(audio)
    if not text:
        return ""
    print(f"Toi (voix): {text}")
    return None if _is_exit(text) else text


def _run_turn(channel: Channel, messages: list, user_text: str) -> None:
    stream = channel.open_stream() if (channel.speak_replies and channel.open_stream) else None
    channel.stream = stream
    if channel.drives_avatar:
        avatar_state.set_state("thinking")

    reply = run_streamed_turn(
        messages,
        user_text,
        process=lambda msgs, **kw: process_user_message(msgs, voice=channel.speak_replies, **kw),
        stream=stream,
        on_wait=channel.say_waiting,
    )
    channel.stream = None
    if reply:
        channel.deliver(reply, already_spoken=stream is not None)


def _run_session(channel: Channel) -> None:
    """Generic session loop: read the input, process the message, deliver the reply."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        while True:
            if channel.drives_avatar:
                avatar_state.set_state("listening")
            user_text = channel.get_input()
            if user_text is None:
                break
            if not user_text:
                continue
            _run_turn(channel, messages, user_text)
    except KeyboardInterrupt:
        pass
    finally:
        channel.deliver("Au revoir !")


def _discord_process(messages: list, **kwargs) -> str:
    """One agent turn for Discord (messages and calls); callers may override the context or voice flag."""
    kwargs.setdefault("context", DISCORD_CONTEXT)
    kwargs.setdefault("voice", False)
    return process_user_message(messages, **kwargs)


def _start_discord_listener():
    """Start listening to Discord if DISCORD_LISTEN=true; return the active listener or None."""
    if not settings.DISCORD_LISTEN:
        return None
    # Imported late: discord.py is only required when listening is enabled.
    from discord.discord_listener import DiscordListener

    listener = DiscordListener(_discord_process)
    return listener if listener.start() else None


def _start_background_watchers(channel: Channel, discord_listener=None) -> list[threading.Event]:
    """Start Monika's background thread (morning briefing, scheduled tasks) and return its stop events."""

    def _deliver_background(result: str, icon: str) -> None:
        channel.deliver(result, icon=icon)
        if discord_listener is not None and settings.DISCORD_FORWARD_SCHEDULED:
            discord_listener.notify_owner(f"{icon} {result}")  # the user may not be at the terminal

    # A finished background task speaks up the same way a scheduled task does.
    background_jobs.set_notifier(lambda text: _deliver_background(text, "🧵"))

    def _run_briefing_if_due() -> None:
        moment = briefing.now()
        if not briefing.is_due(moment):
            return
        briefing.mark_delivered(moment)  # before running: a crash must not repeat it every 30 seconds
        print("☀️ [Briefing] Preparing the morning briefing...")
        try:
            result = process_user_message(
                [{"role": "user", "content": briefing.REQUEST}],
                interactive=False,
                context=briefing.context(),
                voice=channel.speak_replies,
            )
        except Exception as e:
            result = f"⚠️ Échec du briefing du matin : {e}"
        channel.deliver(result, icon="☀️")
        if discord_listener is not None and settings.BRIEFING_FORWARD_DISCORD:
            discord_listener.notify_owner(f"☀️ {result}")

    def _scheduler_tick() -> None:
        _run_briefing_if_due()
        for task_id, instruction in pop_due_tasks():
            print(f"🗓️ [Tâche planifiée #{task_id}] Exécution : {instruction}")
            task_messages = [{"role": "user", "content": instruction}]
            try:
                result = process_user_message(
                    task_messages,
                    interactive=False,
                    context=SCHEDULED_TASK_CONTEXT,
                    voice=channel.speak_replies,
                )
            except Exception as e:
                result = f"⚠️ Échec de la tâche planifiée #{task_id} : {e}"
            _deliver_background(result, "🗓️")

    watchers = [start_watcher(settings.SCHEDULER_CHECK_INTERVAL_SECONDS, _scheduler_tick)]
    if settings.RAG_ENABLED:
        from rag.indexer import start_rag_indexing

        watchers.append(start_rag_indexing())  # incremental document indexing in the background
    return watchers


def _run_monika(channel: Channel, greeting: str) -> None:
    """Run a complete Monika session (watchers, loop, cleanup) for a given `Channel`."""
    print(greeting)
    # Memories initialise in the background (embedding model load, Hindsight connection) while the
    # avatar and the voice start: they are ready long before the first message.
    memory_manager.start()
    start_avatar_server()
    if channel.speak_replies:
        speak("Bonjour, je t'écoute.")

    discord_listener = _start_discord_listener()
    stop_events = _start_background_watchers(channel, discord_listener)
    try:
        _run_session(channel)
    finally:
        background_jobs.set_notifier(None)  # do not deliver to a session that is closing
        for stop_event in stop_events:
            stop_event.set()
        if discord_listener is not None:
            discord_listener.stop()
        memory_manager.shutdown()  # sends the last exchanges to the memories before quitting


def run_monika() -> None:
    """Run Monika in text mode in the terminal."""
    channel = Channel(get_input=_read_text_input, speak_replies=False, drives_avatar=True)
    _run_monika(channel, greeting="🤖 Monika initialisée. Comment puis-je t'aider ?")


def run_monika_voice() -> None:
    """Run Monika in voice mode: listen, answer out loud sentence by sentence while the model writes."""
    from voice import voice_tts
    from voice.streaming_tts import SpeechStream

    channel = Channel(get_input=_read_voice_input, speak_replies=True, drives_avatar=False)
    if settings.VOICE_STREAM_TTS:
        channel.open_stream = lambda: SpeechStream(voice_tts.synthesize, voice_tts.play_synthesized)
    _run_monika(channel, greeting="Monika (mode vocal) initialisée. Parle, je t'écoute.")
