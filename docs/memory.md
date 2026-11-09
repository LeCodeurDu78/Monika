# Personal memory and documents

## Personal memory

An **evidence memory**: **events** (your messages and Monika's
replies) and durable **facts**, each justified by a verbatim quote. Everything
lives in `~/.config/monika/memory/notes.json` (JSON, `0600`); embeddings are optional.

- **What she keeps**: identity, tastes, habits, projects, relationships, hardware, decisions and
  constraints. No episode summary: a fact is a short sentence, and its evidence is the exact
  sentence you said.
- **Evidence**: the director copies the quote; the code checks that it really exists in the text
  of the same speaker before recording the fact. A paraphrase is discarded (and counted in the
  logs). `/memory`, the tools and the *Memory* panel show the evidence.
- **Corrections**: a fact that corrects another marks it `superseded` and points to the new one.
  The old one is not erased: the prompt shows both versions in order, with the mention
  "earlier version, corrected since".
- **Capture**: after each exchange (message of at least `MEMORY_CAPTURE_MIN_CHARS` characters),
  the event is written in the background, without slowing the reply. Every
  `MEMORY_CONSOLIDATE_TURNS` turns (6 by default), the director extracts at most 6 facts from the batch. It
  runs in the **reflection** role (`MEMORY_LLM_MODEL`, otherwise `REFLECTION_MODEL`, otherwise
  `MODEL_NAME`); `MEMORY_CAPTURE_ENABLED=false` turns capture off.
- **Recall**: on each message, a **hybrid** search ranks facts and episodes:
  a home-grown BM25 (keywords, accent-insensitive) + fastembed embeddings, merged by rank. The block
  injected into `<memoire>` remains natural language, with perceived dates ("yesterday", "3
  days ago") and the quotes. Budget: `MEMORY_NOTES_MAX_CHARS` (1800 by default).
- **Embeddings**: `MEMORY_EMBEDDINGS_ENABLED=true` by default; the model is
  `MEMORY_EMBEDDING_MODEL` (empty = `RAG_EMBEDDING_MODEL`, so a single download for both
  memory and documents). It loads on the first captured exchange, not at startup. Without it,
  keyword recall continues and says so in the logs.
- **Failure**: if the director fails or returns unreadable JSON, the extraction cursor does not move:
  the batch will be retried on the next exchange. Nothing is lost.
- **Management**: `/memory`, `/forget ID`, `memoire_*` tools, or the *Memory* panel of the
  dashboard. A fact edited by hand loses its quote and becomes a "no evidence" note;
  deleting a correction reactivates the old version.
- **Old notebook**: a `notes.json` from before this version is archived as
  `notes.legacy-<date>.json` on first launch; it is not read again. Old mem0/Qdrant data
  (`memory/qdrant`) is no longer read either.

## Personal documents (RAG)

llama-index + Qdrant in **local mode** (`~/.config/monika/rag/qdrant`, no Docker), fastembed
embeddings on the CPU, **hybrid** search (meaning + keywords) then **reranking**.

- **Index**: `python -m monika --rag-index [folder ...]`, `/index` in the chat, or
  the `documents_indexer` tool. By default all of your personal folder (`RAG_ROOTS`).
  Indexing is **incremental**: a SQLite manifest remembers each file (date, size,
  fingerprint); only new or modified files are reprocessed, deleted files
  are removed from the index, a file that was merely "touched" is not re-embedded. It can be
  interrupted (Ctrl+C or `/quit`) and resumes where it left off. Follow it with `/documents` or
  `--rag-status`.
- **Formats**: text (`.txt .md .rst .json .yaml …`), code (`.py .js .rs .sh`, `PKGBUILD`,
  `Makefile`, …), PDF (one page = one position) and Word `.docx` (paragraphs and tables).
  **Scanned PDFs** go through RapidOCR: pages whose text layer is shorter than
  `RAG_OCR_MIN_CHARS` are rendered with pypdfium2 and recognised on the CPU (`RAG_OCR_DPI`,
  `RAG_OCR_MAX_PAGES`). It is on by default (`RAG_OCR_ENABLED=true`); if `rapidocr-onnxruntime`
  or `pypdfium2` is missing, the text layer is still indexed and the error is reported, never fatal.
- **Secrets excluded by default**: hidden files and folders, `.env*`, keys (`*.pem`, `*.key`,
  `id_rsa*`…), `.ssh/ .gnupg/ .aws/ .kube/`, `credentials*`, `secrets.*`, `*.kdbx`, `*.tfvars`,
  `.netrc`…; plus `node_modules`, `.git`, caches and Monika's data folder. A file whose **content**
  looks like a secret (private key, AWS key, GitHub or Slack token, `sk-` key, Google key) is skipped
  entirely. The report says how many files were skipped and why. `RAG_EXCLUDE` adds your own
  patterns; `RAG_INCLUDE_HIDDEN=true` includes hidden files (never secrets).
- **Search**: meaning (vectors) and keywords (home-grown BM25: accent-insensitive, understands
  `parse_http_request` as `parseHttpRequest`, no model to download) are merged, then
  a reranker reads each candidate with the question. Each result is cited with its **path
  and position**: `~/notes/project.md — lines 10–25`, `~/docs/contract.pdf — page 2`,
  `~/docs/minutes.docx — paragraphs 3–5`. Without an available reranker, search continues without it
  and says so.
- **Score filter**: after reranking, passages whose score is below `RAG_MIN_SCORE`
  (0 by default) are discarded, before keeping the `RAG_TOP_K` best ones; the tool and
  `--rag-search` say how many were discarded, and if nothing passes the threshold the model receives
  "no sufficiently relevant passage" instead of noise. The threshold is on the reranker's scale,
  so it does not apply when reranking is unavailable (the message says so). To tune the value, look at the scores:
  `RAG_MIN_SCORE=-100 python -m monika --rag-search your question`.
- **Models** (first use, connection required): embeddings ~1 GB, reranker ~1.1 GB, all
  on the CPU; the VRAM stays free for voice.
- **Limits**: the embedding model reads only ~128 tokens, hence short passages (500
  characters); the keyword part sees the whole passage. Local Qdrant compares vectors one by one:
  comfortable up to a few hundred thousand passages, beyond that a real server would be needed.
  Changing the model or the chunk size requires
  `python -m monika --rag-index --reset` (otherwise Monika refuses to open the index, with this
  message). Indexed content may be hostile: like any tool result, it
  reaches the model as data, never as orders.
