"""Memory tools: mem0 (quick facts about the user) and Hindsight (long-term memory)."""

from datetime import datetime

from memory.common import MemoryUnavailable, describe_error, truncate
from memory.manager import memory_manager

# An explicit call may wait for mem0 to initialise (model download on first launch).
TOOL_WAIT_SECONDS = 30.0
MAX_OUTPUT_CHARS = 6000

_TYPE_LABELS = {"world": "fait", "experience": "vécu", "observation": "synthèse"}


def _format_facts(items: list[dict], title: str, empty: str) -> str:
    if not items:
        return f"🧠 {empty}"
    lines = [f"- {str(item.get('memory', '')).strip()}  (id: {item.get('id')})" for item in items]
    return truncate(f"🧠 {title} ({len(items)}) :\n" + "\n".join(lines), MAX_OUTPUT_CHARS)


def user_memory(action: str, text: str = "", memory_id: str = "") -> str:
    """Quick memory about the user (mem0): remember, search, list or forget a fact."""
    mem0 = memory_manager.mem0
    if mem0 is None:
        return "❌ La mémoire rapide (mem0) est désactivée (MEM0_ENABLED=false)."

    try:
        if action == "add":
            if not text.strip():
                return "❌ 'text' est requis : le fait à retenir (ex: \"Ma sœur s'appelle Léa\")."
            saved = mem0.add([{"role": "user", "content": text.strip()}], wait=TOOL_WAIT_SECONDS)
            saved = [item for item in saved if item.get("event") in ("ADD", "UPDATE") and item.get("memory")]
            if not saved:
                return "ℹ️ Rien de nouveau à retenir (information déjà connue, ou pas assez durable)."
            return "✅ Retenu :\n" + "\n".join(f"- {item['memory']}" for item in saved)

        if action == "search":
            if not text.strip():
                return "❌ 'text' est requis : ce qu'il faut chercher dans la mémoire."
            hits = mem0.search(text.strip(), limit=10, wait=TOOL_WAIT_SECONDS, timeout=TOOL_WAIT_SECONDS)
            return _format_facts(hits, "Souvenirs trouvés", "Aucun souvenir correspondant.")

        if action == "list":
            return _format_facts(
                mem0.list_all(limit=30, wait=TOOL_WAIT_SECONDS), "Ce que je sais de l'utilisateur", "La mémoire rapide est vide."
            )

        if action == "delete":
            if not memory_id.strip():
                return "❌ 'memory_id' est requis (obtiens-le d'abord avec action='search' ou 'list')."
            mem0.delete(memory_id.strip(), wait=TOOL_WAIT_SECONDS)
            return f"🗑️ Souvenir {memory_id.strip()} oublié."

    except MemoryUnavailable as exc:
        return f"❌ {exc}"
    except Exception as exc:
        return f"❌ Erreur de la mémoire rapide : {describe_error(exc)}"

    return "❌ Action inconnue. Actions valides : 'add', 'search', 'list', 'delete'."


def deep_memory(action: str, query: str = "", content: str = "") -> str:
    """Long-term memory (Hindsight): reflect on memories, recall them, or store one."""
    hindsight = memory_manager.hindsight
    if hindsight is None:
        return "❌ La mémoire longue (Hindsight) est désactivée (HINDSIGHT_ENABLED=false)."

    try:
        if action == "reflect":
            if not query.strip():
                return "❌ 'query' est requis : la question à laquelle répondre à partir des souvenirs."
            answer = hindsight.reflect(query.strip())
            return truncate(f"🧠 {answer}", MAX_OUTPUT_CHARS) if answer else "🧠 Aucun souvenir exploitable sur ce sujet."

        if action == "recall":
            if not query.strip():
                return "❌ 'query' est requis : ce qu'il faut rechercher dans les souvenirs."
            memories = hindsight.recall(query.strip())
            if not memories:
                return "🧠 Aucun souvenir correspondant."
            lines = []
            for item in memories:
                label = _TYPE_LABELS.get(item["type"], item["type"])
                tags = ", ".join(part for part in (item["date"], label) if part)
                lines.append(f"- {item['text'].strip()}" + (f"  ({tags})" if tags else ""))
            return truncate(f"🧠 Souvenirs trouvés ({len(memories)}) :\n" + "\n".join(lines), MAX_OUTPUT_CHARS)

        if action == "retain":
            if not content.strip():
                return "❌ 'content' est requis : l'information à enregistrer."
            hindsight.retain(
                content.strip(),
                context="Information consignée explicitement par Monika à la demande de l'utilisateur.",
                tags=["explicite"],
                when=datetime.now().astimezone(),
            )
            return "✅ Information confiée à la mémoire longue (elle est analysée en arrière-plan)."

    except MemoryUnavailable as exc:
        return f"❌ {exc}"
    except Exception as exc:
        return f"❌ Erreur de la mémoire longue : {describe_error(exc)}"

    return "❌ Action inconnue. Actions valides : 'reflect', 'recall', 'retain'."
