"""Incremental indexing: scan folders, compare with the manifest, (re)index only what changed."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from monika.config import Settings
from rag.chunking import chunk_document
from rag.filters import PathFilter
from rag.loaders import SkipFile, classify, load
from rag.manifest import FileRow
from rag.store import RagStore, build_nodes, file_id_for


@dataclass(frozen=True)
class Candidate:
    path: Path
    kind: str
    mtime_ns: int
    size: int


@dataclass
class SyncProgress:
    """Live counters of a sync run, readable from anywhere while it works."""

    state: str = "inactif"  # inactif | analyse | indexation | terminé | annulé | échec
    started_at: float = 0.0
    finished_at: float = 0.0
    total: int = 0  # Files to (re)index in this run
    processed: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    chunks: int = 0
    current: str = ""
    skipped: Counter = field(default_factory=Counter)  # Reason -> files (this run)
    excluded: Counter = field(default_factory=Counter)  # Reason -> files not even considered
    secret_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.state in ("analyse", "indexation")

    def summary(self) -> str:
        elapsed = (self.finished_at or time.time()) - self.started_at if self.started_at else 0
        parts = [f"État : {self.state}"]
        if self.state == "indexation":
            parts.append(f"{self.processed}/{self.total} fichiers traités"
                         + (f" (en cours : {self.current})" if self.current else ""))
        elif self.state in ("terminé", "annulé"):
            parts.append(f"{self.processed}/{self.total} fichiers traités")
        parts.append(f"{self.added} nouveau(x), {self.updated} modifié(s), {self.deleted} supprimé(s), "
                     f"{self.unchanged} inchangé(s), {self.chunks} passages ajoutés")
        if self.skipped:
            parts.append("ignorés : " + ", ".join(f"{n} {r}" for r, n in self.skipped.most_common(5)))
        if self.secret_files:
            parts.append(f"{len(self.secret_files)} fichier(s) de secrets exclus par nom")
        if self.errors:
            parts.append(f"{len(self.errors)} erreur(s), dernière : {self.errors[-1]}")
        if elapsed:
            parts.append(f"durée : {elapsed:.0f} s")
        return ". ".join(parts) + "."


def scan(roots: list[Path], filt: PathFilter) -> tuple[list[Candidate], Counter, list[str]]:
    """Walk the roots (blocking) and return indexable files, exclusion counts and secret names."""
    found: list[Candidate] = []
    excluded: Counter = Counter()
    secrets: list[str] = []
    for root in roots:
        if not root.is_dir():
            excluded["dossier introuvable"] += 1
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            keep = []
            for name in dirnames:
                sub = Path(dirpath) / name
                reason = "lien symbolique" if sub.is_symlink() else filt.dir_reason(sub)
                if reason:
                    excluded[reason] += 1
                else:
                    keep.append(name)
            dirnames[:] = keep
            for name in filenames:
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue
                reason = filt.file_reason(path)
                if reason:
                    excluded[reason] += 1
                    if reason == "fichier de secrets":
                        secrets.append(str(path))
                    continue
                kind = classify(path)
                if kind is None:
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                found.append(Candidate(path, kind, st.st_mtime_ns, st.st_size))
    return found, excluded, secrets


def _sha256(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


class Indexer:
    """Runs one incremental sync at a time against a RagStore."""

    def __init__(self, store: RagStore, settings: Settings, paths_home: Path,
                 display_home: Path | None = None) -> None:
        self._store = store
        self._s = settings
        self._monika_home = paths_home
        self._home = display_home or Path.home()

    def display(self, path: Path) -> str:
        try:
            return "~/" + str(path.relative_to(self._home))
        except ValueError:
            return str(path)

    def _filter(self) -> PathFilter:
        return PathFilter(self._s.rag_exclude_patterns(), self._s.rag_include_hidden,
                          forbidden=[self._monika_home])

    async def sync(self, roots: list[Path], progress: SyncProgress) -> SyncProgress:
        """Bring the index in line with the folders; counters are updated as it goes."""
        manifest = self._store.manifest
        progress.__init__()  # Fresh counters for this run
        progress.state, progress.started_at = "analyse", time.time()
        try:
            candidates, excluded, secrets = await asyncio.to_thread(scan, roots, self._filter())
            progress.excluded, progress.secret_files = excluded, secrets[:50]
            known = manifest.all()
            seen = {str(c.path) for c in candidates}
            todo: list[Candidate] = []
            for c in candidates:
                row = known.get(str(c.path))
                if row and row.mtime_ns == c.mtime_ns and row.size == c.size and row.status != "error":
                    progress.unchanged += 1
                else:
                    todo.append(c)
            gone = [row for p, row in known.items()
                    if p not in seen and any(Path(p).is_relative_to(r) for r in roots)]
            progress.total, progress.state = len(todo), "indexation"
            for row in gone:
                if row.status == "indexed":
                    await self._store.delete_file(row.file_id)
                manifest.delete(row.path)
                progress.deleted += 1
            for c in todo:
                progress.current = self.display(c.path)
                await self._index_file(c, known.get(str(c.path)), progress)
                progress.processed += 1
                await asyncio.sleep(0)  # Let searches and the chat run between files
            progress.state, progress.current = "terminé", ""
        except asyncio.CancelledError:
            progress.state, progress.current = "annulé", ""
            raise
        except Exception as exc:
            progress.state, progress.current = "échec", ""
            progress.errors.append(f"{type(exc).__name__}: {exc}"[:300])
        finally:
            progress.finished_at = time.time()
        return progress

    async def _index_file(self, c: Candidate, previous: FileRow | None, progress: SyncProgress) -> None:
        path = str(c.path)
        file_id = file_id_for(path)
        had_vectors = bool(previous and previous.status == "indexed")
        # An interrupted update leaves an "error" row: its old passages may still be stored
        maybe_vectors = bool(previous and previous.status in ("indexed", "error"))

        def record(status: str, chunks: int = 0, detail: str = "", sha: str = "") -> None:
            self._store.manifest.upsert(FileRow(path, file_id, c.mtime_ns, c.size, sha, chunks, status, detail))

        sha = ""
        try:
            sha = await asyncio.to_thread(_sha256, c.path)
            if had_vectors and previous.sha256 == sha:  # Touched but identical: nothing to redo
                record("indexed", previous.chunks, "", sha)
                progress.unchanged += 1
                return
            loaded = await asyncio.to_thread(load, c.path, c.kind, self._s.rag_max_text_kb,
                                             self._s.rag_max_doc_mb)
            chunks = chunk_document(loaded, self._s.rag_chunk_chars, self._s.rag_chunk_overlap_chars)
            if not chunks:
                raise SkipFile("aucun passage exploitable")
            if len(chunks) > self._s.rag_max_chunks_per_file:
                raise SkipFile(f"trop de passages ({len(chunks)}, limite RAG_MAX_CHUNKS_PER_FILE)")
            from llama_index.core.schema import MetadataMode  # Heavy import, only when indexing

            nodes = build_nodes(path, self.display(c.path), c.kind, chunks)
            texts = [n.get_content(MetadataMode.EMBED) for n in nodes]
            batch = self._s.rag_embed_batch
            vectors: list[list[float]] = []
            for i in range(0, len(texts), batch):
                vectors += await self._store.embed_texts(texts[i:i + batch])
            for node, vec in zip(nodes, vectors):
                node.embedding = vec
            # Swap old for new only now: a failure above leaves the previous version searchable.
            # The row says "to redo" during the swap, so an interruption is repaired by the next sync.
            if maybe_vectors:
                record("error", 0, "mise à jour interrompue", sha)
                await self._store.delete_file(file_id)
            await self._store.add_nodes(nodes)
            record("indexed", len(nodes), "", sha)
            progress.chunks += len(nodes)
            progress.updated += 1 if had_vectors else 0
            progress.added += 0 if had_vectors else 1
        except SkipFile as exc:
            if maybe_vectors:
                await self._store.delete_file(file_id)
            reason = str(exc)
            record("skipped", 0, reason, sha)
            progress.skipped[reason.split(" (")[0]] += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # One broken file never stops the run; it is retried next time
            message = f"{self.display(c.path)} : {type(exc).__name__}: {exc}"[:300]
            record("error", 0, message, sha)
            progress.errors.append(message)
