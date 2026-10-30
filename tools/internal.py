"""Tools implemented inside Monika itself (as opposed to MCP tools)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.context import ToolOutputStore
from agent.tasks import TaskStore, TasksError, parse_when
from memory.fast import FastMemory, MemoryUnavailable
from rag.service import RagError, RagService
from tools.registry import Tool, ToolFailure, ToolRegistry


def _int(args: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        raise ToolFailure(f"« {key} » doit être un entier.") from None


def register_result_reader(registry: ToolRegistry, store: ToolOutputStore) -> None:
    """Expose lire_resultat so truncated tool results can be read piece by piece."""

    async def handler(args: dict[str, Any]) -> str:
        result_id = str(args.get("id", ""))
        start = _int(args, "debut", 0)
        length = _int(args, "longueur", store.max_chars)
        text, total = store.read(result_id, start, length)
        end = start + len(text)
        tail = "fin du résultat" if end >= total else f"suite à partir du caractère {end}"
        return f"{text}\n[caractères {start}–{end} sur {total} ; {tail}]"

    registry.register(Tool(
        name="lire_resultat",
        description=("Lit un passage d'un résultat d'outil tronqué, sauvegardé sur disque. "
                     "Utilise l'id indiqué dans le message de troncature."),
        parameters={"type": "object", "properties": {
            "id": {"type": "string", "description": "Identifiant du résultat sauvegardé"},
            "debut": {"type": "integer", "description": "Premier caractère à lire (0 par défaut)"},
            "longueur": {"type": "integer",
                         "description": f"Nombre de caractères (max {store.max_chars})"}},
            "required": ["id"]},
        handler=handler,
        source="interne",
    ))


def _guarded(fn):
    """Turn memory backend failures into plain-text tool errors the model can read."""
    async def wrapper(args: dict[str, Any]) -> str:
        try:
            return await fn(args)
        except (MemoryUnavailable, RagError) as exc:
            raise ToolFailure(str(exc)) from exc
        except ToolFailure:
            raise
        except Exception as exc:
            raise ToolFailure(f"Erreur : {type(exc).__name__}: {exc}") from exc
    return wrapper


def register_memory_tools(registry: ToolRegistry, memory: FastMemory) -> None:
    """Expose the fast memory: remember, search, list and forget facts about the user."""

    async def retenir(args: dict[str, Any]) -> str:
        fact = str(args.get("fait", "")).strip()
        if not fact:
            raise ToolFailure("« fait » est vide.")
        if len(fact) > 500:
            raise ToolFailure("« fait » dépasse 500 caractères : résume-le en une phrase.")
        item = await memory.add_fact(fact)
        return f"Retenu : « {item.text} » (id {item.id})."

    async def chercher(args: dict[str, Any]) -> str:
        query = str(args.get("requete", "")).strip()
        if not query:
            raise ToolFailure("« requete » est vide.")
        items = await memory.search(query, limit=_int(args, "limite", 8))
        if not items:
            return "Aucun souvenir correspondant."
        return "\n".join(f"- {i.text} [id {i.id}, pertinence {i.score:.2f}]" if i.score is not None
                         else f"- {i.text} [id {i.id}]" for i in items)

    async def lister(args: dict[str, Any]) -> str:
        items = await memory.list_all(limit=_int(args, "limite", 50))
        if not items:
            return "La mémoire rapide est vide."
        return "\n".join(f"- {i.text} [id {i.id}]" for i in items)

    async def oublier(args: dict[str, Any]) -> str:
        memory_id = str(args.get("id", "")).strip()
        if not memory_id:
            raise ToolFailure("« id » est vide : cherche d'abord le souvenir pour obtenir son id.")
        text = await memory.delete(memory_id)
        if text is None:
            raise ToolFailure(f"Aucun souvenir avec l'id {memory_id}.")
        return f"Oublié : « {text} »."

    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required}

    for name, description, params, fn in (
        ("memoire_retenir",
         "Retient durablement un fait ou une préférence sur l'utilisateur (identité, goûts, "
         "habitudes, projets perso, matériel). À utiliser quand il demande de retenir quelque "
         "chose ou dit un fait important ; les faits ordinaires sont aussi captés automatiquement.",
         obj({"fait": {"type": "string", "description": "Une phrase, à la 3e personne"}}, ["fait"]),
         retenir),
        ("memoire_chercher",
         "Cherche dans la mémoire rapide des faits et préférences sur l'utilisateur.",
         obj({"requete": {"type": "string"},
              "limite": {"type": "integer", "description": "Nombre max de résultats (8 par défaut)"}},
             ["requete"]),
         chercher),
        ("memoire_lister", "Liste les faits et préférences retenus sur l'utilisateur.",
         obj({"limite": {"type": "integer", "description": "Nombre max (50 par défaut)"}}, []), lister),
        ("memoire_oublier",
         "Supprime un souvenir de la mémoire rapide à partir de son id (obtenu via memoire_chercher "
         "ou memoire_lister).",
         obj({"id": {"type": "string"}}, ["id"]), oublier),
    ):
        registry.register(Tool(name=name, description=description, parameters=params,
                               handler=_guarded(fn), source="mémoire"))


def register_task_tools(registry: ToolRegistry, store: TaskStore) -> None:
    """Expose the scheduled tasks: the assistant can set a reminder when the user asks for one."""

    async def planifier(args: dict[str, Any]) -> str:
        texte = str(args.get("texte", "")).strip()
        quand = str(args.get("quand", "")).strip()
        try:
            when = parse_when(quand)
            task = store.add(texte, when)
        except TasksError as exc:
            raise ToolFailure(str(exc)) from exc
        repeat = " (elle se répétera)" if task.recurring else ""
        return f"Tâche {task.id} créée : {when.says} — « {task.prompt} »{repeat}."

    async def lister(args: dict[str, Any]) -> str:
        tasks = store.all()
        if not tasks:
            return "Aucune tâche planifiée."
        return "\n".join([f"{len(tasks)} tâche(s) planifiée(s) :"] + [t.describe() for t in tasks])

    async def annuler(args: dict[str, Any]) -> str:
        task_id = str(args.get("id", "")).strip()
        if not task_id:
            raise ToolFailure("« id » est vide : utilise taches_lister pour obtenir l'id.")
        task = store.cancel(task_id)
        if task is None:
            raise ToolFailure(f"Aucune tâche avec l'id {task_id}.")
        return f"Tâche {task.id} supprimée : « {task.prompt} »."

    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required}

    for name, description, params, fn in (
        ("taches_planifier",
         "Crée un rappel ou une tâche récurrente qui se déclenchera plus tard, même sans conversation "
         "en cours : à ce moment-là, tu recevras le texte comme une consigne et tu répondras. À "
         "utiliser quand l'utilisateur dit « rappelle-moi… », « préviens-moi… », « tous les matins… ».",
         obj({"quand": {"type": "string",
                       "description": "« dans 20 minutes », « 18h30 », « demain 8h », "
                                      "« toutes les 2 heures », « tous les 3 jours », "
                                      "« chaque jour à 8h », « chaque lundi et jeudi à 9h », "
                                      "« du lundi au vendredi à 9h »"},
              "texte": {"type": "string",
                        "description": "Ce que tu devras faire ou dire à ce moment-là, à la 2e personne"}},
             ["quand", "texte"]),
         planifier),
        ("taches_lister", "Liste les rappels et tâches récurrentes programmés, avec leur date et leur id.",
         obj({}, []), lister),
        ("taches_annuler", "Supprime une tâche planifiée à partir de son id (obtenu via taches_lister).",
         obj({"id": {"type": "string"}}, ["id"]), annuler),
    ):
        registry.register(Tool(name=name, description=description, parameters=params,
                               handler=fn, source="tâches"))


def register_document_tools(registry: ToolRegistry, rag: RagService) -> None:
    """Expose the personal-documents index: search (with citations), start indexing, status."""

    async def chercher(args: dict[str, Any]) -> str:
        query = str(args.get("requete", "")).strip()
        if not query:
            raise ToolFailure("« requete » est vide.")
        limit = max(1, min(_int(args, "limite", rag.default_top_k), 20))
        outcome = await rag.search(query, limit)
        if outcome.index_empty:
            if rag.running:
                return f"Aucun document n'est encore indexé, mais l'indexation est en cours. {rag.progress.summary()}"
            return "Aucun document n'est indexé. Lance documents_indexer (ou la commande /indexer) d'abord."
        if not outcome.hits:
            if outcome.filtered:
                return (f"Aucun passage assez pertinent : {outcome.filtered} passage(s) écarté(s) sous le "
                        f"seuil de score ({outcome.min_score:g}, réglage RAG_MIN_SCORE).")
            return "Aucun passage trouvé."
        how = "recherche hybride + reranking" if outcome.reranked else "recherche hybride"
        lines = [f"{len(outcome.hits)} passage(s) trouvé(s) ({how}). Cite le chemin et la position :"]
        for i, hit in enumerate(outcome.hits, start=1):
            lines.append(f"\n[{i}] {hit.citation()} (score {hit.score:.2f})\n{hit.text}")
        if outcome.warning:
            lines.append(f"\n[Note : {outcome.warning}]")
        if outcome.filtered:
            lines.append(f"\n[{outcome.filtered} passage(s) moins pertinent(s) écarté(s) par le filtre de score.]")
        if rag.running:
            lines.append(f"\n[L'indexation est en cours ({rag.progress.processed}/{rag.progress.total} "
                         "fichiers) : les résultats peuvent être incomplets.]")
        return "\n".join(lines)

    async def indexer(args: dict[str, Any]) -> str:
        folder = str(args.get("dossier", "")).strip()
        roots = None
        if folder:
            path = Path(folder).expanduser()
            if not path.is_dir():
                raise ToolFailure(f"Dossier introuvable : {folder}")
            roots = [path.resolve()]
        if rag.start_sync(roots):
            return ("Indexation lancée en arrière-plan : elle ne traite que les fichiers nouveaux ou "
                    "modifiés. Utilise documents_statut pour suivre l'avancement.")
        return f"Une indexation est déjà en cours. {rag.progress.summary()}"

    async def statut(args: dict[str, Any]) -> str:
        return await rag.status_text()

    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required}

    registry.register(Tool(
        name="documents_chercher",
        description=("Cherche dans les fichiers personnels indexés de l'utilisateur (notes, texte, "
                     "PDF, Word, code) par le sens ET par mots-clés. Renvoie des passages avec leur "
                     "chemin et leur position (lignes, page ou paragraphes) à citer."),
        parameters=obj({"requete": {"type": "string", "description": "Ce que tu cherches, en langage naturel"},
                        "limite": {"type": "integer", "description": "Nombre de passages (5 par défaut, 20 max)"}},
                       ["requete"]),
        handler=_guarded(chercher), slow=True, source="documents",
        filler_variants=["Je fouille dans tes documents, une seconde.",
                         "Je cherche ça dans tes fichiers.",
                         "Je regarde dans tes notes, ça ne devrait pas être long."]))
    registry.register(Tool(
        name="documents_indexer",
        description=("Lance en arrière-plan l'indexation (incrémentale) des dossiers personnels, ou d'un "
                     "dossier précis. Les fichiers secrets et cachés sont exclus par défaut."),
        parameters=obj({"dossier": {"type": "string", "description": "Dossier à indexer (par défaut : les dossiers configurés)"}}, []),
        handler=_guarded(indexer), source="documents"))
    registry.register(Tool(
        name="documents_statut",
        description="Donne l'état de l'index de documents : nombre de fichiers, avancement, exclusions.",
        parameters=obj({}, []), handler=_guarded(statut), source="documents"))
