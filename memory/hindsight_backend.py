"""Monika's long-term memory (Hindsight): projects, decisions, events, past exchanges.

Hindsight is a server (see docker-compose.hindsight.yml) spoken to through its Python client. That client's
synchronous methods use one asyncio loop PER THREAD, while Monika calls the memory from several threads
(session, scheduled tasks, background saving): a client created in one thread is unusable in another. Every
call therefore goes through ONE dedicated asyncio loop living in its own thread.
"""

import asyncio
import concurrent.futures
import threading
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from core.settings import settings
from memory.common import MemoryUnavailable, describe_error, log

# After a connection failure, do not insist during this delay (avoids slowing every turn).
COOLDOWN_SECONDS = 30.0

# What Hindsight should retain: extraction is driven by an LLM, hence by these instructions.
RETAIN_MISSION = (
    "Extrais uniquement ce qui a de la valeur à long terme pour Monika, assistante IA personnelle : "
    "projets de l'utilisateur et leur avancement, décisions prises et leurs raisons, problèmes "
    "rencontrés et solutions trouvées, préférences de travail, événements importants, personnes et "
    "relations, engagements et échéances, enseignements tirés d'une réussite ou d'un échec. "
    "Ignore le bavardage, les politesses, les commandes ponctuelles (volume, ouverture d'une "
    "application, météo, musique) et les résultats bruts d'outils. Rédige chaque fait en français, de "
    "façon autonome (nom du projet, de la personne et date inclus quand ils sont connus)."
)

# What Hindsight should do when asked to "reflect".
REFLECT_MISSION = (
    "Tu es la mémoire à long terme de Monika, une assistante IA personnelle. Tu réponds à partir des "
    "souvenirs disponibles sur l'utilisateur, ses projets, ses décisions et vos échanges passés : "
    "synthétise, relie les éléments entre eux, et dis clairement ce qui est incertain ou absent plutôt "
    "que d'inventer. Réponds en français, de façon concise."
)


def _is_api_error(exc: BaseException) -> bool:
    """True if the Hindsight server answered with an HTTP error (so it is reachable)."""
    try:
        from hindsight_client_api.exceptions import ApiException
    except ImportError:
        return False
    return isinstance(exc, ApiException)


class HindsightMemory:
    """Client Hindsight utilisable depuis n'importe quel thread."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Any = None  # created in the dedicated loop, never elsewhere
        self._bank_ready = False
        self._guard = threading.Lock()
        self._started = False
        self._down_until = 0.0
        self._last_error = ""
        self._fatal: Optional[str] = None  # permanent error (client not installed...)

    @property
    def bank_id(self) -> str:
        return settings.HINDSIGHT_BANK_ID

    # --- cycle de vie ----------------------------------------------------------------
    def start(self) -> None:
        """Check the connection in the background and prepare the memory bank (no effect if already started)."""
        with self._guard:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._check, daemon=True, name="hindsight-check").start()

    def _check(self) -> None:
        try:
            version = self._run(lambda client: client.aget_version(), settings.HINDSIGHT_TIMEOUT)
            log(
                f"🧠 [Mémoire] Hindsight prêt ({settings.HINDSIGHT_BASE_URL}, banque « {self.bank_id} », "
                f"API {getattr(version, 'api_version', '?')})."
            )
        except MemoryUnavailable as exc:
            log(f"⚠️ [Mémoire] {exc} La mémoire longue sera réessayée automatiquement.")

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            if self._client is not None:
                asyncio.run_coroutine_threadsafe(self._client.aclose(), loop).result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)

    # --- plomberie asyncio -------------------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._guard:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, daemon=True, name="hindsight-loop").start()
                self._loop = loop
            return self._loop

    async def _get_client(self) -> Any:
        if self._client is None:
            from hindsight_client import Hindsight  # late import: optional dependency

            self._client = Hindsight(
                base_url=settings.HINDSIGHT_BASE_URL,
                api_key=settings.HINDSIGHT_API_KEY,
                # The effective timeout is `_run`'s; this one is only a safety net.
                timeout=max(settings.HINDSIGHT_TIMEOUT, settings.HINDSIGHT_REFLECT_TIMEOUT),
                max_attempts=2,
            )
        return self._client

    async def _ensure_bank(self, client: Any) -> None:
        """Create Monika's bank (or update its instructions). Idempotent, done once."""
        if self._bank_ready:
            return
        try:
            await client.acreate_bank(
                self.bank_id,
                retain_mission=RETAIN_MISSION,
                reflect_mission=REFLECT_MISSION,
            )
        except Exception as exc:
            if not _is_api_error(exc):
                raise  # connection problem: we will retry later
            # Older/stricter server: the bank will be created on the first retain, without instructions.
            log(f"⚠️ [Mémoire] Hindsight : consignes de la banque non appliquées ({exc}).")
        self._bank_ready = True

    def _run(self, make_call: Callable[[Any], Awaitable[Any]], timeout: float) -> Any:
        """Run `make_call(client)` on the dedicated loop and wait for the result for at most `timeout` seconds."""
        if self._fatal:
            raise MemoryUnavailable(self._fatal)
        remaining = self._down_until - time.monotonic()
        if remaining > 0:
            raise MemoryUnavailable(
                f"Hindsight injoignable ({self._last_error}) ; nouvelle tentative dans {int(remaining) + 1} s."
            )

        async def _task() -> Any:
            client = await self._get_client()
            await self._ensure_bank(client)
            return await make_call(client)

        future = asyncio.run_coroutine_threadsafe(_task(), self._ensure_loop())
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise MemoryUnavailable(f"Hindsight n'a pas répondu en {timeout:.0f} s.") from None
        except ImportError as exc:
            self._fatal = f"Hindsight indisponible : {describe_error(exc)}"
            raise MemoryUnavailable(self._fatal) from exc
        except Exception as exc:
            if _is_api_error(exc):  # the server answered: it is reachable, the request is at fault
                raise MemoryUnavailable(f"Hindsight a refusé la requête : {exc}") from exc
            self._last_error = describe_error(exc)
            self._down_until = time.monotonic() + COOLDOWN_SECONDS
            raise MemoryUnavailable(
                f"Hindsight injoignable ({settings.HINDSIGHT_BASE_URL}) : {self._last_error}."
            ) from exc

    # --- operations --------------------------------------------------------------------
    def retain(
        self,
        content: str,
        *,
        context: Optional[str] = None,
        tags: Optional[list[str]] = None,
        document_id: Optional[str] = None,
        when: Optional[datetime] = None,
    ) -> None:
        """Hand `content` to Hindsight. Fact extraction happens server-side, in the background."""
        self._run(
            lambda client: client.aretain(
                bank_id=self.bank_id,
                content=content,
                context=context,
                tags=tags,
                document_id=document_id,
                timestamp=when,
                retain_async=True,
            ),
            settings.HINDSIGHT_TIMEOUT,
        )

    def recall(
        self,
        query: str,
        *,
        types: Optional[list[str]] = None,
        budget: str = "mid",
        max_tokens: int = 2000,
    ) -> list[dict]:
        """Most relevant raw memories: [{'text', 'type', 'date', 'context'}]."""
        response = self._run(
            lambda client: client.arecall(
                bank_id=self.bank_id,
                query=query,
                types=types,
                budget=budget,
                max_tokens=max_tokens,
            ),
            settings.HINDSIGHT_TIMEOUT,
        )
        return [
            {
                "text": item.text,
                "type": item.type or "",
                "date": (item.occurred_start or item.mentioned_at or "")[:10],
                "context": item.context or "",
            }
            for item in response.results
        ]

    def reflect(self, query: str, *, context: Optional[str] = None, budget: str = "low") -> str:
        """Answer to `query`, reasoned over the whole set of memories."""
        response = self._run(
            lambda client: client.areflect(
                bank_id=self.bank_id,
                query=query,
                context=context,
                budget=budget,
            ),
            settings.HINDSIGHT_REFLECT_TIMEOUT,
        )
        return (response.text or "").strip()
