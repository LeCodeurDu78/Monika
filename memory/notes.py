"""Mémoire à preuves : des faits cités, des corrections, un rappel hybride.

Un échange est archivé comme **événement** (les deux voix). Tous les
`MEMORY_CONSOLIDATE_TURNS` échanges, un directeur extrait des **faits** : chacun porte une
citation recopiée mot pour mot, vérifiée ici contre l'événement source. Une correction ne
supprime pas l'ancien fait : elle le marque `superseded` et pointe vers le nouveau, pour que
le prompt montre les deux versions dans l'ordre.

Le rappel est hybride : BM25 maison (mots-clés, sans accent) + embeddings fastembed, fusionnés
par rang (RRF). Sans embeddings disponibles, BM25 seul continue. Le bloc rendu reste du
langage naturel, jamais du JSON ni des identifiants.

shortcut: vecteurs stockés dans le JSON, passer à un sidecar binaire au-delà de ~10 000 entrées.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.roles import REFLECTION_TEMPERATURE, memory_model
from monika.config import Settings
from monika.i18n import is_english, tr
from rag.lexical import tokenize

log = logging.getLogger("memory")

VERSION = 2
MAX_FACT_CHARS = 500
MAX_QUOTE_CHARS = 400
MAX_EVENT_CHARS = 1200
MAX_EXTRACT_EVENTS = 12
MAX_FACTS_PER_PASS = 6
MAX_FACTS_IN_PROMPT = 60
MAX_QUOTE_RENDER = 300
EVIDENCE_MIN_CHARS = 12
RRF_K = 60
MAX_PENDING_EMBED = 64

_WORD = re.compile(r"[0-9a-zà-öø-ÿ]{2,}", re.IGNORECASE)
_TAG = re.compile(r"<\s*/?\s*memoire\s*>", re.IGNORECASE)
_MONTHS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_EMBEDDERS: dict[str, Any] = {}

_DIRECTOR_SYSTEM = (
    "Tu es le directeur de mémoire de Monika. Tu extrais des faits durables sur "
    "l'utilisateur, chacun justifié par une citation littérale. Tu ne résumes pas les "
    "épisodes, tu n'inventes rien et tu ne donne aucune consigne de style. Réponds "
    "uniquement par un objet JSON."
)


class MemoryUnavailable(Exception):
    """La mémoire ne peut pas fonctionner ; le message est en français."""


@dataclass(frozen=True)
class MemoryItem:
    """Un fait du magasin, tel que la page et les outils le voient."""

    id: str
    text: str
    score: float | None = None
    created_at: str | None = None
    kind: str = ""
    quote: str = ""  # la phrase exacte qui justifie l'entrée
    status: str = ""  # active, superseded, retracted…


def _empty() -> dict[str, Any]:
    return {
        "version": VERSION,
        "events": [],
        "facts": [],
        "vectors": {},
        "total_turns": 0,
        "extracted_events": 0,
        "last_updated": None,
    }


def _clean(value: Any, limit: int = 400) -> str:
    """Une ligne de texte simple, balises neutralisées, longueur bornée."""
    text = _TAG.sub("", str(value or ""))
    text = " ".join(text.split())
    return text[:limit]


def _norm(text: str) -> str:
    """Comparaison de texte sans casse, accents ni ponctuation."""
    folded = unicodedata.normalize("NFKD", (text or "").lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(_WORD.findall(folded))


def quote_supported(quote: str, said: str) -> bool:
    """Vrai si la citation est réellement dans les mots de l'utilisateur, et assez précise."""
    needle = _norm(quote)
    return len(needle) >= EVIDENCE_MIN_CHARS and needle in _norm(said)


def _json_object(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _epoch(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stamp(at: float | None) -> str | None:
    return (
        datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds")
        if at
        else None
    )


def _when(at: float) -> str:
    """Une date perçue : aujourd'hui, hier, il y a N jours, le 12 mars."""
    moment = datetime.fromtimestamp(at).astimezone()
    now = datetime.now().astimezone()
    days = (now.date() - moment.date()).days
    english = is_english()
    if days <= 0:
        return "today" if english else "aujourd'hui"
    if days == 1:
        return "yesterday" if english else "hier"
    if days < 7:
        return f"{days} days ago" if english else f"il y a {days} jours"
    months = _MONTHS_EN if english else _MONTHS_FR
    day = f"{months[moment.month - 1]} {moment.day}"
    if moment.year != now.year:
        day += f", {moment.year}"
    return f"on {day}" if english else f"le {day}"


def _next_id(prefix: str, items: list[dict[str, Any]]) -> str:
    used = 0
    for item in items:
        ident = str(item.get("id") or "")
        if ident.startswith(prefix) and ident[len(prefix) :].isdigit():
            used = max(used, int(ident[len(prefix) :]))
    return f"{prefix}{used + 1}"


def _sync_embed(model: str, texts: list[str]) -> list[list[float]]:
    """Charge le modèle une fois (thread), puis rend les vecteurs demandés."""
    from fastembed import TextEmbedding

    encoder = _EMBEDDERS.get(model)
    if encoder is None:
        encoder = TextEmbedding(model_name=model)
        _EMBEDDERS[model] = encoder
    return [[float(x) for x in vector] for vector in encoder.embed(texts)]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = math.fsum(x * y for x, y in zip(left, right))
    norm_left = math.sqrt(math.fsum(x * x for x in left))
    norm_right = math.sqrt(math.fsum(y * y for y in right))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _bm25(
    query_tokens: list[str], documents: list[tuple[str, list[str]]]
) -> dict[str, float]:
    """BM25 maison, sur les mots déjà normalisés (accents retirés par tokenize)."""
    if not query_tokens or not documents:
        return {}
    k1, b = 1.2, 0.75
    frequencies = Counter()
    for _, tokens in documents:
        frequencies.update(set(tokens))
    total = len(documents)
    average = sum(len(tokens) for _, tokens in documents) / total
    scores: dict[str, float] = {}
    for ident, tokens in documents:
        counts = Counter(tokens)
        score = 0.0
        for term in set(query_tokens):
            if term not in counts:
                continue
            df = frequencies[term]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            tf = counts[term]
            score += idf * tf * (k1 + 1) / (
                tf + k1 * (1 - b + b * len(tokens) / max(average, 1))
            )
        if score > 0:
            scores[ident] = score
    return scores


def _fuse(bm25: dict[str, float], vectors: dict[str, float]) -> dict[str, float]:
    """Reciprocal Rank Fusion : le rang compte, pas l'échelle des deux scores."""
    fused: dict[str, float] = {}
    for rank, (ident, _) in enumerate(
        sorted(bm25.items(), key=lambda item: item[1], reverse=True)
    ):
        fused[ident] = fused.get(ident, 0.0) + 1 / (RRF_K + rank + 1)
    for rank, (ident, score) in enumerate(
        sorted(vectors.items(), key=lambda item: item[1], reverse=True)
    ):
        if score > 0:
            fused[ident] = fused.get(ident, 0.0) + 1 / (RRF_K + rank + 1)
    return fused


def _fact_searchable(fact: dict[str, Any]) -> str:
    quotes = " ".join(str(q.get("quote") or "") for q in fact.get("quotes") or [])
    return f"{fact.get('text') or ''} {quotes} {fact.get('kind') or ''}"


def _event_searchable(event: dict[str, Any]) -> str:
    return f"{event.get('user') or ''} {event.get('monika') or ''}"


class EvidenceMemory:
    """Le magasin : événements, faits cités, corrections et rappel hybride."""

    def __init__(self, path: Path, settings: Settings, llm: Any = None) -> None:
        self._path = path
        self._s = settings
        self._llm = llm
        self._lock = asyncio.Lock()
        self._busy = False
        self._embedder_ready = False
        self._embed_warned = False
        self._archive_legacy()

    def bind_llm(self, llm: Any) -> None:
        """Le directeur a besoin du LLM, créé après la mémoire au démarrage."""
        self._llm = llm

    # --- stockage ------------------------------------------------------------------
    def _archive(self, label: str) -> Path | None:
        """Déplace le fichier actuel sous un nom daté ; jamais l'écraser."""
        if not self._path.exists():
            return None
        suffix = time.strftime("%Y%m%d-%H%M%S")
        stem = f"{self._path.stem}.{label}-{suffix}"
        archive = self._path.with_name(f"{stem}{self._path.suffix}")
        counter = 1
        while archive.exists():  # deux archivages dans la même seconde ne s'écrasent pas
            archive = self._path.with_name(
                f"{stem}-{counter}{self._path.suffix}"
            )
            counter += 1
        try:
            os.replace(self._path, archive)
            archive.chmod(0o600)
        except OSError as exc:
            log.warning("Mémoire : archivage impossible (%s).", exc)
            return None
        return archive

    def _archive_legacy(self) -> None:
        """Un carnet d'avant la version 2 est mis de côté, pas écrasé."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("version") == VERSION:
            return
        archive = self._archive("legacy")
        if archive is not None:
            log.info("Ancien carnet archivé sous %s ; la mémoire repart à vide.", archive)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Mémoire illisible (%s) : je repars à vide.", exc)
            return _empty()
        if not isinstance(data, dict) or data.get("version") != VERSION:
            return _empty()
        state = _empty()
        state["events"] = [
            event
            for event in data.get("events") or []
            if isinstance(event, dict) and event.get("id") and event.get("at") is not None
        ]
        state["facts"] = [
            fact
            for fact in data.get("facts") or []
            if isinstance(fact, dict) and fact.get("id") and fact.get("text")
        ]
        vectors = data.get("vectors")
        if isinstance(vectors, dict):
            state["vectors"] = {
                str(ident): [float(x) for x in vector]
                for ident, vector in vectors.items()
                if isinstance(vector, list) and vector
            }
        state["total_turns"] = int(data.get("total_turns") or 0)
        state["extracted_events"] = min(
            int(data.get("extracted_events") or 0), len(state["events"])
        )
        return state

    def _save(self, state: dict[str, Any]) -> None:
        """Écriture atomique et privée : une mémoire à moitié écrite ne doit jamais rester."""
        state["version"] = VERSION
        state["last_updated"] = time.time()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp, self._path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    # --- embeddings -----------------------------------------------------------------
    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self._s.memory_embeddings_enabled or not texts:
            return []
        model = (
            self._s.memory_embedding_model.strip() or self._s.rag_embedding_model
        )
        try:
            vectors = await asyncio.to_thread(_sync_embed, model, texts)
        except Exception as exc:  # noqa: BLE001 - BM25 reste disponible
            if not self._embed_warned:
                self._embed_warned = True
                log.warning(
                    "Mémoire : embeddings indisponibles (%s: %s) ; rappel par mots-clés.",
                    type(exc).__name__,
                    exc,
                )
            return []
        self._embedder_ready = True
        return vectors

    async def _embed_pending(self) -> None:
        """Vectorise les événements et faits qui n'ont pas encore de vecteur."""
        state = self._load()
        pending: list[tuple[str, str]] = []
        for event in state["events"]:
            ident = str(event["id"])
            if ident not in state["vectors"]:
                pending.append((ident, _event_searchable(event)))
        for fact in state["facts"]:
            ident = str(fact["id"])
            if ident not in state["vectors"]:
                pending.append((ident, _fact_searchable(fact)))
        if not pending:
            return
        pending = pending[:MAX_PENDING_EMBED]
        vectors = await self._embed_texts([text for _, text in pending])
        if not vectors:
            return
        dimension = len(vectors[0])
        async with self._lock:
            fresh = self._load()
            if any(
                len(vector) != dimension for vector in fresh["vectors"].values()
            ):  # un autre modèle a été choisi : les anciens vecteurs ne comparent plus rien
                fresh["vectors"] = {}
            for (ident, _), vector in zip(pending, vectors):
                fresh["vectors"][ident] = vector
            self._save(fresh)

    async def _query_vector(self, query: str) -> list[float] | None:
        if not query.strip() or not self._embedder_ready:
            return None
        try:
            vectors = await asyncio.wait_for(self._embed_texts([query]), 5)
        except TimeoutError:
            return None
        return vectors[0] if vectors else None

    # --- capture et extraction ------------------------------------------------------
    async def capture(self, user_text: str, assistant_text: str) -> list[str]:
        """Archive l'échange et déclenche l'extraction quand le lot est prêt."""
        user = _clean(user_text, MAX_EVENT_CHARS)
        if not user:
            return []
        now = time.time()
        async with self._lock:
            state = self._load()
            state["events"].append(
                {
                    "id": _next_id("e", state["events"]),
                    "at": now,
                    "user": user,
                    "monika": _clean(assistant_text, MAX_EVENT_CHARS),
                }
            )
            state["total_turns"] = int(state.get("total_turns") or 0) + 1
            self._save(state)
            pending = len(state["events"]) - int(state.get("extracted_events") or 0)
        await self._embed_pending()
        if pending >= self._s.memory_consolidate_turns:
            await self.consolidate()
        return []

    async def consolidate(self) -> bool:
        """Extrait les faits du prochain lot ; rien n'est perdu si le directeur échoue."""
        if self._llm is None or self._busy:
            return False
        async with self._lock:
            state = self._load()
            cursor = int(state.get("extracted_events") or 0)
            events = state["events"][cursor : cursor + MAX_EXTRACT_EVENTS]
            facts = [
                fact
                for fact in state["facts"]
                if fact.get("status") != "superseded"
            ][-MAX_FACTS_IN_PROMPT:]
        if len(events) < self._s.memory_consolidate_turns:
            return False
        self._busy = True
        try:
            data = await self._ask_director(events, facts)
            if data is None:
                return False
            await self._merge(data, events)
            async with self._lock:
                fresh = self._load()
                fresh["extracted_events"] = cursor + len(events)
                self._save(fresh)
            await self._embed_pending()
            return True
        finally:
            self._busy = False

    async def _ask_director(
        self, events: list[dict[str, Any]], facts: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        state_text = (
            "\n".join(
                f"- [{fact['id']}] {_clean(fact.get('text'), 300)}"
                + (
                    f" — « {_clean(fact['quotes'][0].get('quote'), 200)} »"
                    if fact.get("quotes")
                    else ""
                )
                for fact in facts
            )
            or "(aucun fait pour l'instant)"
        )
        events_text = "\n".join(
            f"- [{event['id']}] "
            f"({datetime.fromtimestamp(_epoch(event['at'])).strftime('%Y-%m-%d %H:%M')})\n"
            f"  utilisateur : {_clean(event.get('user'), 700)}\n"
            f"  Monika : {_clean(event.get('monika'), 500)}"
            for event in events
        )
        prompt = (
            "Faits déjà retenus :\n"
            f"{state_text}\n\n"
            "Nouveaux échanges :\n"
            f"{events_text}\n\n"
            "Extrais au plus 6 faits durables sur l'utilisateur : identité, goûts, "
            "habitudes, projets, relations, matériel, décisions, contraintes. Ignore le "
            "bavardage, les questions et les consignes sur la façon de répondre. Chaque "
            "fait porte au moins une citation recopiée mot pour mot depuis le texte du "
            "même locuteur ; une paraphrase sera rejetée. Utilise `supersedes` (liste "
            "d'ids de faits déjà retenus) uniquement quand le nouveau fait corrige "
            "l'ancien. Si rien de durable n'est dit, réponds {\"facts\": []}.\n\n"
            "Format exact :\n"
            '{"facts": [{"text": "fait en français, au présent", '
            '"kind": "user_fact", "quotes": [{"source_id": "e12", '
            '"speaker": "user", "quote": "phrase exacte"}], '
            '"supersedes": []}]}\n'
            "`kind` vaut user_fact pour un fait sur lui, assistant_commitment pour une "
            "promesse de Monika."
        )
        try:
            completion = await asyncio.wait_for(
                self._llm.ask(
                    [
                        {"role": "system", "content": _DIRECTOR_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=REFLECTION_TEMPERATURE,
                    model=memory_model(self._s),
                ),
                timeout=90,
            )
        except Exception as exc:  # noqa: BLE001 - la mémoire est best effort
            log.warning("Directeur de mémoire indisponible : %s", exc)
            return None
        data = _json_object(completion.content)
        if data is None:
            log.warning("Le directeur de mémoire n'a pas rendu de JSON exploitable.")
        return data

    async def _merge(
        self, data: dict[str, Any], events: list[dict[str, Any]]
    ) -> list[str]:
        """Fusionne la réponse du directeur ; toute entrée sans preuve est écartée."""
        by_event = {str(event["id"]): event for event in events}
        raw_facts = data.get("facts")
        entries = raw_facts if isinstance(raw_facts, list) else []
        dropped = 0
        async with self._lock:
            state = self._load()
            existing = {str(fact["id"]): fact for fact in state["facts"]}
            active_texts = {
                _norm(fact.get("text"))
                for fact in state["facts"]
                if fact.get("status", "active") == "active"
            }
            created: list[dict[str, Any]] = []
            for entry in entries[:MAX_FACTS_PER_PASS]:
                if not isinstance(entry, dict):
                    dropped += 1
                    continue
                text = _clean(
                    entry.get("text") or entry.get("texte"), MAX_FACT_CHARS
                )
                if not text:
                    dropped += 1
                    continue
                kind = (
                    "engagement"
                    if str(entry.get("kind") or entry.get("type") or "")
                    in ("assistant_commitment", "engagement", "promesse")
                    else "fait"
                )
                quotes: list[dict[str, str]] = []
                for quote in entry.get("quotes") or entry.get("citations") or []:
                    if not isinstance(quote, dict):
                        dropped += 1
                        continue
                    source = by_event.get(
                        str(quote.get("source_id") or quote.get("evenement") or "")
                    )
                    speaker = (
                        "assistant"
                        if str(quote.get("speaker") or "").lower() == "assistant"
                        else "user"
                    )
                    citation = _clean(
                        quote.get("quote") or quote.get("citation"), MAX_QUOTE_CHARS
                    )
                    if source is None or not quote_supported(
                        citation, str(source.get(speaker) or "")
                    ):
                        dropped += 1
                        continue
                    quotes.append(
                        {
                            "source_id": str(source["id"]),
                            "speaker": speaker,
                            "quote": citation,
                        }
                    )
                if not quotes:
                    dropped += 1
                    continue
                if kind == "fait" and not any(
                    quote["speaker"] == "user" for quote in quotes
                ):
                    dropped += 1
                    continue
                if _norm(text) in active_texts:
                    dropped += 1
                    continue
                supersedes: list[str] = []
                raw_supersedes = entry.get("supersedes")
                if isinstance(raw_supersedes, str):
                    raw_supersedes = [raw_supersedes]
                for old_id in raw_supersedes if isinstance(raw_supersedes, list) else []:
                    old = existing.get(str(old_id))
                    if old is not None and old.get("status", "active") == "active":
                        supersedes.append(str(old["id"]))
                fact = {
                    "id": _next_id("f", state["facts"] + created),
                    "text": text,
                    "kind": kind,
                    "at": max(
                        _epoch(by_event[q["source_id"]].get("at")) for q in quotes
                    ),
                    "created_at": time.time(),
                    "source_ids": list(
                        dict.fromkeys(q["source_id"] for q in quotes)
                    ),
                    "quotes": quotes,
                    "status": "active",
                    "supersedes": supersedes,
                }
                created.append(fact)
                active_texts.add(_norm(text))
            for fact in created:
                for old_id in fact["supersedes"]:
                    old = existing.get(old_id)
                    if old is not None:
                        old["status"] = "superseded"
                        old["superseded_by"] = fact["id"]
            state["facts"].extend(created)
            self._save(state)
        if dropped:
            log.info("Mémoire : %d entrée(s) écartée(s), citation absente ou invalide.", dropped)
        return [fact["id"] for fact in created]

    # --- lecture --------------------------------------------------------------------
    def _rank(
        self,
        query: str,
        facts: list[dict[str, Any]],
        events: list[dict[str, Any]],
        vectors: dict[str, list[float]],
        query_vector: list[float] | None,
    ) -> tuple[dict[str, float], dict[str, float]]:
        query_tokens = tokenize(query)
        fact_docs = [
            (str(fact["id"]), tokenize(_fact_searchable(fact))) for fact in facts
        ]
        event_docs = [
            (str(event["id"]), tokenize(_event_searchable(event)))
            for event in events
        ]
        fact_bm25 = _bm25(query_tokens, fact_docs)
        event_bm25 = _bm25(query_tokens, event_docs)
        fact_vectors: dict[str, float] = {}
        event_vectors: dict[str, float] = {}
        if query_vector:
            fact_vectors = {
                ident: _cosine(query_vector, vectors[ident])
                for ident, _ in fact_docs
                if ident in vectors
            }
            event_vectors = {
                ident: _cosine(query_vector, vectors[ident])
                for ident, _ in event_docs
                if ident in vectors
            }
        return _fuse(fact_bm25, fact_vectors), _fuse(event_bm25, event_vectors)

    async def recall(self, query: str = "") -> str:
        """Le bloc à injecter dans le prompt : des faits, leurs preuves, les corrections."""
        state = self._load()
        facts = [
            fact for fact in state["facts"] if fact.get("status") != "retracted"
        ]
        events = state["events"]
        if not facts and not events:
            return ""
        query_vector = await self._query_vector(query) if query.strip() else None
        if query.strip():
            fact_scores, event_scores = self._rank(
                query, facts, events, state["vectors"], query_vector
            )
            fact_ids = list(fact_scores)
            event_ids = list(event_scores)
        else:
            fact_ids = [
                str(fact["id"])
                for fact in sorted(facts, key=lambda f: _epoch(f.get("at")), reverse=True)
            ]
            event_ids = [
                str(event["id"]) for event in reversed(events)
            ]
        return self._render(state, fact_ids, event_ids)

    def _fact_line(self, fact: dict[str, Any]) -> str:
        text = _clean(fact.get("text"), MAX_FACT_CHARS)
        quotes = fact.get("quotes") or []
        when = _when(_epoch(fact.get("at")) or time.time())
        if fact.get("manual") or fact.get("kind") == "manuel":
            line = f"- {when} — note saisie à la main (sans citation) : {text}"
            if fact.get("status") == "superseded":
                line += " (version antérieure, corrigée depuis)"
            return line
        quote = next(
            (q for q in quotes if q.get("speaker") == "user"),
            quotes[0] if quotes else {},
        )
        citation = _clean(quote.get("quote"), MAX_QUOTE_RENDER)
        if fact.get("kind") == "engagement":
            head = f"- {when}, j'ai promis : « {citation} »" if citation else f"- {when} : {text}"
        else:
            head = f"- {when}, tu m'as dit : « {citation} »" if citation else f"- {when} : {text}"
        if text and _norm(text) not in _norm(citation):
            head += f" — {text}"
        if fact.get("status") == "superseded":
            head += " (version antérieure, corrigée depuis)"
        return head[:600]

    def _fact_order(
        self, by_id: dict[str, dict[str, Any]], fact_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Inclut l'ancienne version avant sa correction, sans doublon."""
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ident in fact_ids:
            fact = by_id.get(ident)
            if fact is None:
                continue
            for old_id in fact.get("supersedes") or []:
                old = by_id.get(str(old_id))
                if old is not None and old["id"] not in seen:
                    ordered.append(old)
                    seen.add(old["id"])
            if fact["id"] not in seen:
                ordered.append(fact)
                seen.add(fact["id"])
            successor = by_id.get(str(fact.get("superseded_by") or ""))
            if successor is not None and successor["id"] not in seen:
                ordered.append(successor)
                seen.add(successor["id"])
        return ordered

    def _render(
        self, state: dict[str, Any], fact_ids: list[str], event_ids: list[str]
    ) -> str:
        budget = max(300, int(self._s.memory_notes_max_chars))
        by_fact = {str(fact["id"]): fact for fact in state["facts"]}
        by_event = {str(event["id"]): event for event in state["events"]}
        lines: list[str] = []
        used = 0
        for fact in self._fact_order(by_fact, fact_ids)[:8]:
            line = self._fact_line(fact)
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1
        for ident in event_ids[:4]:
            event = by_event.get(ident)
            if event is None:
                continue
            user = _clean(event.get("user"), MAX_QUOTE_RENDER)
            if not user:
                continue
            line = f"- {_when(_epoch(event['at']))}, tu m'as dit : « {user} »"
            monika = _clean(event.get("monika"), 200)
            if monika:
                line += f" ; j'ai répondu : « {monika} »"
            if used + len(line) + 1 > budget:
                break
            lines.append(line[:700])
            used += len(line) + 1
        if not lines:
            return ""
        header = (
            "What I know about you (quoted facts come from your own words):"
            if is_english()
            else "Ce que je sais de toi (les faits cités viennent de tes phrases) :"
        )
        return header + "\n" + "\n".join(lines)

    async def list_all(self, limit: int = 50) -> list[MemoryItem]:
        """Faits du plus récent au plus ancien, avec leur preuve et leur état."""
        state = self._load()
        facts = sorted(
            state["facts"], key=lambda fact: _epoch(fact.get("at")), reverse=True
        )
        items: list[MemoryItem] = []
        for fact in facts[: max(1, limit)]:
            quotes = fact.get("quotes") or []
            quote = next(
                (q.get("quote") for q in quotes if q.get("speaker") == "user"),
                quotes[0].get("quote") if quotes else "",
            )
            items.append(
                MemoryItem(
                    str(fact["id"]),
                    _clean(fact.get("text"), MAX_FACT_CHARS),
                    created_at=_stamp(_epoch(fact.get("at"))),
                    kind=str(fact.get("kind") or "fait"),
                    quote=_clean(quote, 240),
                    status=str(fact.get("status") or "active"),
                )
            )
        return items

    async def changes_since(self, since_ts: float) -> dict[str, Any]:
        """Ce qui a bougé depuis une date, pour la veille nocturne."""
        state = self._load()
        facts = [fact for fact in state["facts"] if _epoch(fact.get("created_at")) >= since_ts]
        events = [event for event in state["events"] if _epoch(event.get("at")) >= since_ts]
        active = [
            _clean(fact.get("text"), 300)
            for fact in state["facts"]
            if fact.get("status", "active") == "active"
        ]
        return {
            "notes": " ; ".join(active[:8])[:700],
            "notes_rewrites": len(facts),
            "topics": active[:8],
            "events": [
                f"{_when(_epoch(event['at']))} : {_clean(event.get('user'), 160)}"
                for event in events[-6:]
            ],
            "total_turns": int(state.get("total_turns") or 0),
        }

    @staticmethod
    def guidance(items: list[MemoryItem]) -> str:
        return next(
            (item.text for item in items if item.kind in ("notes", "fait")), ""
        )

    async def search(self, query: str, limit: int = 8) -> list[MemoryItem]:
        """Rappel hybride, pour l'outil `memoire_chercher`."""
        query = (query or "").strip()
        if not query:
            return await self.list_all(limit)
        state = self._load()
        facts = [
            fact for fact in state["facts"] if fact.get("status") != "retracted"
        ]
        query_vector = await self._query_vector(query)
        fact_scores, _ = self._rank(
            query, facts, [], state["vectors"], query_vector
        )
        ranked = sorted(fact_scores.items(), key=lambda item: item[1], reverse=True)
        if not ranked:
            return []
        best = ranked[0][1] or 1.0
        by_id = {str(fact["id"]): fact for fact in facts}
        items: list[MemoryItem] = []
        for ident, score in ranked[: max(1, limit)]:
            fact = by_id.get(ident)
            if fact is None:
                continue
            quotes = fact.get("quotes") or []
            quote = next(
                (q.get("quote") for q in quotes if q.get("speaker") == "user"),
                quotes[0].get("quote") if quotes else "",
            )
            items.append(
                MemoryItem(
                    str(fact["id"]),
                    _clean(fact.get("text"), MAX_FACT_CHARS),
                    score=round(score / best, 2) if best else None,
                    created_at=_stamp(_epoch(fact.get("at"))),
                    kind=str(fact.get("kind") or "fait"),
                    quote=_clean(quote, 240),
                    status=str(fact.get("status") or "active"),
                )
            )
        return items

    # --- écriture manuelle ----------------------------------------------------------
    async def add_fact(self, text: str) -> MemoryItem:
        """Ajoute une note saisie à la main (sans citation, donc affichée comme telle)."""
        fact = _clean(text, MAX_FACT_CHARS)
        if not fact:
            raise MemoryUnavailable(tr("memory.empty"))
        async with self._lock:
            state = self._load()
            existing = next(
                (
                    item
                    for item in state["facts"]
                    if item.get("status", "active") == "active"
                    and _norm(item.get("text")) == _norm(fact)
                ),
                None,
            )
            if existing is not None:
                return MemoryItem(
                    str(existing["id"]),
                    str(existing["text"]),
                    created_at=_stamp(_epoch(existing.get("at"))),
                    kind=str(existing.get("kind") or "manuel"),
                    status=str(existing.get("status") or "active"),
                )
            now = time.time()
            new_id = _next_id("f", state["facts"])
            state["facts"].append(
                {
                    "id": new_id,
                    "text": fact,
                    "kind": "manuel",
                    "manual": True,
                    "at": now,
                    "created_at": now,
                    "source_ids": [],
                    "quotes": [],
                    "status": "active",
                    "supersedes": [],
                }
            )
            self._save(state)
        await self._embed_pending()
        return MemoryItem(
            new_id, fact, created_at=_stamp(now), kind="manuel", status="active"
        )

    async def update(self, memory_id: str, text: str) -> bool:
        """Réécrit un fait ; la citation d'origine ne le justifie plus, elle est retirée."""
        clean = _clean(text, self._s.memory_notes_max_chars)
        if not clean:
            return False
        async with self._lock:
            state = self._load()
            for fact in state["facts"]:
                if str(fact.get("id")) != memory_id:
                    continue
                fact["text"] = clean
                fact["quotes"] = []
                fact["source_ids"] = []
                fact["kind"] = "manuel"
                fact["manual"] = True
                fact["edited_at"] = time.time()
                self._save(state)
                return True
        return False

    async def delete(self, memory_id: str) -> str | None:
        """Supprime un fait ; une correction supprimée réactive l'ancienne version."""
        async with self._lock:
            state = self._load()
            target = next(
                (
                    fact
                    for fact in state["facts"]
                    if str(fact.get("id")) == memory_id
                ),
                None,
            )
            if target is None:
                return None
            state["facts"] = [
                fact
                for fact in state["facts"]
                if str(fact.get("id")) != memory_id
            ]
            for fact in state["facts"]:
                if memory_id in (fact.get("supersedes") or []):
                    fact["supersedes"] = [
                        ident
                        for ident in fact["supersedes"]
                        if ident != memory_id
                    ]
                if fact.get("superseded_by") == memory_id:
                    fact["status"] = "active"
                    fact.pop("superseded_by", None)
            state["vectors"].pop(memory_id, None)
            self._save(state)
            return _clean(target.get("text"), MAX_FACT_CHARS)

    async def forget_all(self) -> None:
        """Efface tout, après avoir mis une copie de côté."""
        async with self._lock:
            self._archive("before-forget")
            self._save(_empty())

    async def close(self) -> None:
        """Rien à fermer : la mémoire est un simple fichier."""


# L'ancien nom reste l'interface utilisée par l'agent, les outils et la page.
NoteMemory = EvidenceMemory