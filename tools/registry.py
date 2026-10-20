"""Monika's single tool registry."""

import importlib
import inspect
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from tools.system.system_tools import open_application, system_control, get_system_stats
from tools.utils.weather_tools import get_weather
from tools.system.file_tools import manage_files
from tools.system.fs_tools import read_file, write_file, edit_file, grep, glob
from tools.utils.web_tools import fetch_url
from tools.social.email_tools import email_control
from tools.utils.search_tools import web_search
from tools.social.calendar_tools import calendar_control
from tools.utils.project_tools import create_full_project
from tools.system.terminal_tools import run_script
from tools.vision.vision_tools import analyze_image
from tools.meta_tools import create_custom_tool, patch_existing_file
from tools.system.browser_tools import browser_control
from tools.utils.spotify_tools import spotify_control
from tools.social.discord_tools import send_discord_message
from tools.social.call_tools import discord_call
from tools.social.contact_tools import manage_contacts
from tools.utils.scheduler_tools import scheduler_control
from tools.utils.memory_tools import user_memory, deep_memory
from tools.utils.rag_tools import search_documents
from tools.utils.background_tools import background_task
from core.settings import settings


@dataclass
class ToolDef:
    """A tool: its Python function + the description meant for the model (function calling)."""

    func: Callable[..., Any]
    description: str
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)
    # Execution flags (never sent to the model, see to_schema):
    # - lent: the tool takes time; if the call exceeds WAIT_PHRASE_DELAY_SECONDS, Monika says
    #   `phrase_attente` while it runs (one string, or several to pick from at random);
    # - non_fiable: the result comes from outside (web): it is wrapped in <donnees_externes>.
    lent: bool = False
    phrase_attente: "str | tuple[str, ...]" = ""
    non_fiable: bool = False

    @property
    def name(self) -> str:
        return self.func.__name__

    def to_schema(self) -> dict:
        """Build this tool's function-calling schema (OpenAI API format)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": self.required,
                },
            },
        }


TOOL_DEFS: list[ToolDef] = [
    ToolDef(
        open_application,
        "Ouvre une application installée sur l'ordinateur de l'utilisateur.",
        {
            "app_name": {
                "type": "string",
                "description": "Le nom de l'application (ex: notepad, chrome, calc)",
            }
        },
        ["app_name"],
    ),
    ToolDef(
        get_weather,
        "Obtient la météo actuelle pour une ville donnée.",
        {"city": {"type": "string", "description": "Le nom de la ville (ex: Paris, Lyon, Montreal)"}},
        ["city"],
    ),
    ToolDef(
        manage_files,
        "Liste un dossier, crée un dossier ou déplace un fichier. Pour lire, créer ou modifier le contenu d'un fichier, utilise read_file, write_file et edit_file ; pour copier, supprimer ou renommer, utilise run_script.",
        {
            "action": {
                "type": "string",
                "enum": ["list", "create_dir", "move"],
                "description": "L'action à effectuer",
            },
            "path": {"type": "string", "description": "Le chemin du fichier ou dossier à cibler"},
            "target_folder": {
                "type": "string",
                "description": "Le dossier de destination (nécessaire uniquement si action='move')",
            },
        },
        ["action", "path"],
    ),
    ToolDef(
        system_control,
        "Système Linux : volume global (volume_up/volume_down), capture d'écran (screenshot), lecture/pause média générique (media_toggle). Pour Spotify précisément, utilise spotify_control. Pour lire l'écran : screenshot puis analyze_image.",
        {
            "action": {
                "type": "string",
                "enum": ["volume_up", "volume_down", "screenshot", "media_toggle"],
                "description": "L'action système à exécuter.",
            },
            "value": {
                "type": "integer",
                "description": "Pourcentage de variation du volume. Utilisé uniquement pour volume_up/volume_down.",
            },
            "filename": {
                "type": "string",
                "description": (
                    "Nom de fichier pour la capture d'écran (utilisé uniquement pour "
                    "action='screenshot'). L'extension .png est ajoutée si absente. "
                    "Si omis, un nom horodaté est généré automatiquement."
                ),
            },
        },
        ["action"],
    ),
    ToolDef(
        email_control,
        "Permet de lire les derniers e-mails de la boîte de réception ou d'en envoyer un nouveau.",
        {
            "action": {
                "type": "string",
                "enum": ["list", "send"],
                "description": "'list' pour afficher les derniers messages, 'send' pour en envoyer un.",
            },
            "recipient": {
                "type": "string",
                "description": "Adresse e-mail du destinataire (requis si action='send').",
            },
            "subject": {"type": "string", "description": "Sujet de l'e-mail (requis si action='send')."},
            "body": {"type": "string", "description": "Contenu/Corps de l'e-mail (requis si action='send')."},
            "limit": {"type": "integer", "description": "Nombre de derniers e-mails à lire (par défaut 5)."},
        },
        ["action"],
    ),
    ToolDef(
        web_search,
        "Recherche d'information externe et publique : Wikipédia (culture générale, définitions) ou DuckDuckGo (web, actualité). Le paramètre mode (duckduckgo uniquement) oriente la recherche : search, news, research, price, compare. Ne renvoie que des extraits : pour une question de fond, ouvre ensuite les pages pertinentes avec fetch_url.",
        {
            "source": {
                "type": "string",
                "enum": ["wikipedia", "duckduckgo"],
                "description": "'wikipedia' pour de la culture générale ou des définitions, 'duckduckgo' pour de l'actualité ou des recherches Web générales.",
            },
            "query": {"type": "string", "description": "Les termes de la recherche."},
            "mode": {
                "type": "string",
                "enum": ["search", "news", "research", "price", "compare"],
                "description": "Ne s'applique que pour source='duckduckgo'. 'search' (défaut) : recherche web générale. "
                "'news' : actualités récentes via un flux RSS d'actualités. 'research' : recherche "
                "approfondie combinant un résumé encyclopédique et davantage de résultats web. "
                "'price' : oriente la recherche vers des sites marchands pour trouver un prix. "
                "'compare' : oriente la recherche vers des sites de tests/comparatifs.",
            },
        },
        ["source", "query"],
    ),
    ToolDef(
        get_system_stats,
        "Obtient les métriques d'utilisation en temps réel du système (CPU, RAM, Disque, Batterie).",
    ),
    ToolDef(
        calendar_control,
        "Google Calendar : liste, ajoute ou supprime un événement avec horaire (rendez-vous, réunion, cours). Pour un simple rappel sans durée, utilise scheduler_control.",
        {
            "action": {
                "type": "string",
                "enum": ["list", "add", "delete"],
                "description": "'list' pour voir les prochains événements, 'add' pour en créer un nouveau, 'delete' pour en supprimer un existant en le retrouvant par son titre exact (requiert 'summary' ; pour un événement récurrent, supprime toute la série).",
            },
            "summary": {
                "type": "string",
                "description": "Titre du rendez-vous/événement. Requis pour action='add' (le titre à créer) et action='delete' (le titre exact de l'événement à retrouver et supprimer).",
            },
            "start_time": {
                "type": "string",
                "description": "Date et heure de début au format ISO (ex: 2026-08-12T14:00:00).",
            },
            "end_time": {
                "type": "string",
                "description": "Date et heure de fin au format ISO (ex: 2026-08-12T15:00:00).",
            },
            "location": {
                "type": "string",
                "description": "Lieu/salle de l'événement (ex: Salle 102). Optionnel.",
            },
            "repeat_weekly": {
                "type": "boolean",
                "description": "Si True, l'événement se répète chaque semaine le même jour que 'start_time' (ex: pour un cours récurrent). Par défaut False. Utilisé uniquement pour action='add'.",
            },
            "limit": {"type": "integer", "description": "Nombre d'événements à afficher (par défaut 5)."},
        },
        ["action"],
    ),
    ToolDef(
        create_full_project,
        "Initialise un projet complet : crée le dossier dans Code, génère un dépôt GitHub et configure un Vault Obsidian.",
        {
            "project_name": {
                "type": "string",
                "description": "Le nom du projet (ex: monika-v2, api-backend, portfolio)",
            },
            "private": {
                "type": "boolean",
                "description": "True pour rendre le dépôt GitHub privé (par défaut), False pour public.",
            },
        },
        ["project_name"],
    ),
    ToolDef(
        run_script,
        "Exécute une commande Bash locale : git, pip/npm, tests, compilation, scripts Python, copie/suppression/renommage de fichiers, outils en ligne de commande. "
        "Pour lire, écrire ou éditer un fichier, ou chercher dans des fichiers, préfère read_file, write_file, edit_file, grep et glob.",
        {
            "command": {
                "type": "string",
                "description": "La commande Bash exacte à exécuter (ex: 'ls -la', 'python3 script.py', 'git status').",
            },
            "workdir": {
                "type": "string",
                "description": "Le dossier d'exécution de la commande (ex: '/home/adam/Documents/Code/monika'). Par défaut : dossier personnel.",
            },
            "timeout": {
                "type": "integer",
                "description": "Durée maximale en secondes (défaut 120, max 900). À augmenter pour une installation, un build ou une suite de tests longs.",
            },
        },
        ["command"],
    ),
    ToolDef(
        fetch_url,
        "Ouvre une page web et en renvoie le texte complet et lisible (article, documentation, fiche produit...). "
        "À utiliser après web_search pour LIRE les pages dont les liens semblent pertinents, au lieu de te contenter des extraits. "
        "Le contenu long est paginé : si une note indique une suite, relance avec offset. Ne fonctionne pas pour les pages qui exigent JavaScript ou une connexion (utilise alors browser_control).",
        {
            "url": {"type": "string", "description": "URL complète de la page (https://...)."},
            "max_chars": {
                "type": "integer",
                "description": "Nombre maximal de caractères à renvoyer (défaut 15000, max 40000).",
            },
            "offset": {
                "type": "integer",
                "description": "Caractère de départ, pour lire la suite d'une page longue (défaut 0).",
            },
        },
        ["url"],
    ),
    ToolDef(
        read_file,
        "Lit un fichier texte (code, notes, config, logs...) et renvoie ses lignes numérotées. "
        "Les longs fichiers se lisent par tranches avec offset et limit. Lis un fichier avant de le modifier.",
        {
            "path": {"type": "string", "description": "Chemin du fichier (absolu, relatif au dossier personnel, ou avec ~)."},
            "offset": {"type": "integer", "description": "Numéro de la première ligne à lire (défaut 1)."},
            "limit": {"type": "integer", "description": "Nombre maximal de lignes à lire (défaut 2000)."},
        },
        ["path"],
    ),
    ToolDef(
        write_file,
        "Crée un fichier avec le contenu donné, ou écrase entièrement un fichier existant (les dossiers manquants sont créés). "
        "Pour changer seulement une partie d'un fichier existant, utilise edit_file.",
        {
            "path": {"type": "string", "description": "Chemin du fichier à créer ou écraser."},
            "content": {"type": "string", "description": "Le contenu complet du fichier."},
        },
        ["path", "content"],
    ),
    ToolDef(
        edit_file,
        "Modifie un fichier existant en remplaçant un passage exact par un autre. old_string doit correspondre exactement "
        "(espaces et indentation compris) et être unique dans le fichier : ajoute des lignes voisines si besoin, ou utilise replace_all.",
        {
            "path": {"type": "string", "description": "Chemin du fichier à modifier."},
            "old_string": {"type": "string", "description": "Le texte exact à remplacer (copié depuis read_file, sans les numéros de ligne)."},
            "new_string": {"type": "string", "description": "Le texte de remplacement."},
            "replace_all": {
                "type": "boolean",
                "description": "true pour remplacer toutes les occurrences (défaut false : old_string doit alors être unique).",
            },
        },
        ["path", "old_string", "new_string"],
    ),
    ToolDef(
        grep,
        "Cherche une expression régulière dans le contenu des fichiers texte d'un dossier (récursif, ignore .git, node_modules, venv...). "
        "Renvoie fichier:ligne: texte.",
        {
            "pattern": {"type": "string", "description": "Expression régulière Python à chercher (ex: 'def \\w+_tool', 'TODO')."},
            "path": {"type": "string", "description": "Dossier ou fichier où chercher (défaut : dossier personnel)."},
            "include": {"type": "string", "description": "Filtre sur le nom des fichiers (ex: '*.py'). Optionnel."},
            "ignore_case": {"type": "boolean", "description": "true pour ignorer la casse (défaut false)."},
            "context": {"type": "integer", "description": "Lignes de contexte avant/après chaque correspondance (défaut 0, max 10)."},
            "max_results": {"type": "integer", "description": "Nombre maximal de correspondances (défaut 100)."},
        },
        ["pattern"],
    ),
    ToolDef(
        glob,
        "Trouve des fichiers ou dossiers par motif de nom (ex: '**/*.py', 'src/**/test_*.js'), les plus récemment modifiés d'abord.",
        {
            "pattern": {"type": "string", "description": "Motif glob ; '**' parcourt les sous-dossiers."},
            "path": {"type": "string", "description": "Dossier de départ (défaut : dossier personnel)."},
        },
        ["pattern"],
    ),
    ToolDef(
        analyze_image,
        "Analyse un fichier image présent sur le disque (lire du texte ou une erreur, décrire). Pour l'écran actuel : system_control(screenshot) puis cet outil sur le fichier produit.",
        {
            "image_path": {
                "type": "string",
                "description": "Le chemin du fichier image à analyser (ex: '~/Images/screenshot.png').",
            },
            "prompt": {
                "type": "string",
                "description": "Consigne précise sur ce que l'agent doit observer ou chercher dans l'image (ex: 'Que dit ce message d'erreur ?').",
            },
        },
        ["image_path"],
    ),
    ToolDef(
        create_custom_tool,
        "Écrit et sauvegarde un nouvel outil Python réutilisable quand la fonctionnalité n'existe pas.",
        {
            "tool_name": {
                "type": "string",
                "description": "Nom de la fonction Python en snake_case (ex: 'fetch_crypto_price').",
            },
            "python_code": {
                "type": "string",
                "description": "Code Python autonome complet définissant la fonction.",
            },
            "description": {
                "type": "string",
                "description": "Explication de ce que fait la fonction et ses paramètres.",
            },
        },
        ["tool_name", "python_code", "description"],
    ),
    ToolDef(
        patch_existing_file,
        "Modifie un fichier .py du projet Monika selon une instruction en langage naturel (sauvegarde, test, restauration automatique en cas d'échec). À utiliser avec prudence ; pour une nouvelle fonction isolée, préfère create_custom_tool.",
        {
            "file_path": {
                "type": "string",
                "description": "Chemin du fichier .py à modifier, à l'intérieur du projet Monika (absolu ou relatif).",
            },
            "instruction": {
                "type": "string",
                "description": "Instruction précise en langage naturel décrivant la modification à apporter.",
            },
        },
        ["file_path", "instruction"],
    ),
    ToolDef(
        browser_control,
        "Contrôle un navigateur Firefox géré par Monika : onglets, navigation, lecture de page, éléments interactifs, clic, saisie, listes déroulantes, touches, attente d'un élément. Sur une page inconnue, appelle d'abord list_interactive_elements pour obtenir les libellés exacts. navigate et click_element renvoient déjà un aperçu de la page : ne relis pas la page juste après. read_page_content : full=False par défaut, full=True seulement pour extraire un contenu détaillé.",
        {
            "action": {
                "type": "string",
                "enum": [
                    "list_tabs",
                    "switch_tab",
                    "navigate",
                    "read_page_content",
                    "list_interactive_elements",
                    "click_element",
                    "fill_field",
                    "get_field_value",
                    "select_option",
                    "press_key",
                    "wait_for",
                    "close_browser",
                ],
                "description": "L'action à effectuer sur le navigateur.",
            },
            "url": {
                "type": "string",
                "description": "URL de destination (requis pour action='navigate').",
            },
            "tab_id": {
                "type": "integer",
                "description": "Index de l'onglet cible tel qu'affiché par action='list_tabs' (requis pour action='switch_tab').",
            },
            "description": {
                "type": "string",
                "description": "Description de l'élément ciblé : texte visible, libellé du bouton/lien, label du champ, etc. "
                "(requis pour action='click_element', 'fill_field', 'get_field_value' et 'select_option' ; "
                "optionnel pour 'press_key' et 'wait_for').",
            },
            "text": {
                "type": "string",
                "description": "Texte à saisir dans le champ ciblé (requis pour action='fill_field'), ou texte à attendre "
                "sur la page (optionnel pour action='wait_for', alternative à 'description').",
            },
            "full": {
                "type": "boolean",
                "description": "Pour action='read_page_content' uniquement. false (défaut) = aperçu compact "
                "(quelques centaines de caractères), true = contenu complet de la page (coûteux en contexte, "
                "à réserver à l'extraction d'un contenu détaillé).",
            },
            "option": {
                "type": "string",
                "description": "Libellé (ou valeur) de l'option à choisir (requis pour action='select_option').",
            },
            "key": {
                "type": "string",
                "description": "Touche clavier à envoyer, ex: 'Enter', 'Escape', 'Tab', 'ArrowDown' "
                "(requis pour action='press_key').",
            },
            "timeout_ms": {
                "type": "integer",
                "description": "Délai d'attente en millisecondes pour action='wait_for' (défaut 10000).",
            },
        },
        ["action"],
    ),
    ToolDef(
        spotify_control,
        "Contrôle Spotify : lecture, pause, suivant, précédent, volume de l'application (indépendant du volume système) et recherche de morceaux ou playlists.",
        {
            "action": {
                "type": "string",
                "enum": ["play", "pause", "next", "previous", "rewind", "current", "volume"],
                "description": "L'action Spotify à exécuter.",
            },
            "query": {
                "type": "string",
                "description": (
                    "Nom du morceau, artiste ou playlist à chercher (pour action='play'). "
                    "Ouvre Spotify sur les résultats de recherche — sans clé API Spotify "
                    "configurée, Monika ne peut pas lancer directement une piste précise ; "
                    "l'utilisateur devra choisir dans les résultats."
                ),
            },
            "volume": {
                "type": "integer",
                "description": "Le niveau de volume de 0 à 100 (pour action='volume').",
            },
        },
        ["action"],
    ),
    ToolDef(
        manage_contacts,
        "Gère le carnet d'adresses : ajouter, chercher, lister ou supprimer des contacts (Nom -> identifiant Discord). 'add' sur un nom existant met à jour son identifiant.",
        {
            "action": {
                "type": "string",
                "enum": ["add", "get", "list", "delete"],
                "description": "Action à effectuer sur les contacts.",
            },
            "name": {"type": "string", "description": "Le nom du contact (ex: 'Adam', 'Maman')."},
            "discord_id": {
                "type": "string",
                "description": "Identifiant Discord numérique du contact (15 à 22 chiffres). Requis pour action='add'.",
            },
        },
        ["action"],
    ),
    ToolDef(
        send_discord_message,
        "Envoie un message Discord : en message privé à un contact du carnet d'adresses, à un identifiant Discord numérique, ou dans un salon ('channel:<id>'). Sans destinataire, utilise le salon par défaut.",
        {
            "recipient": {
                "type": "string",
                "description": "Nom du contact (ex: 'Adam') OU identifiant Discord numérique OU 'channel:<id du salon>'. Vide = salon par défaut.",
            },
            "message": {"type": "string", "description": "Le texte du message à envoyer."},
        },
        ["message"],
    ),
    ToolDef(
        scheduler_control,
        "Planifie une instruction que Monika exécutera elle-même à l'échéance, une fois, chaque jour ou à intervalle régulier. Convient aux rappels (« rappelle-moi de... »).",
        {
            "action": {
                "type": "string",
                "enum": ["add", "list", "cancel"],
                "description": "'add' pour planifier une tâche, 'list' pour voir les tâches actives, 'cancel' pour en annuler une.",
            },
            "instruction": {
                "type": "string",
                "description": "Ce que Monika doit faire, en langage naturel (ex: 'donne la météo de Paris', 'vérifie mes derniers e-mails'). Requis pour action='add'.",
            },
            "schedule_type": {
                "type": "string",
                "enum": ["once", "daily", "interval"],
                "description": "'once' pour une seule fois (avec 'run_at'), 'daily' pour tous les jours (avec 'time_of_day'), 'interval' pour une boucle récurrente (avec 'interval_seconds'). Requis pour action='add'.",
            },
            "run_at": {
                "type": "string",
                "description": "Date et heure ISO du déclenchement unique (ex: '2026-08-20T09:00:00'), requis si schedule_type='once'.",
            },
            "time_of_day": {
                "type": "string",
                "description": "Heure quotidienne de déclenchement au format 'HH:MM' (ex: '08:00'), requis si schedule_type='daily'.",
            },
            "interval_seconds": {
                "type": "integer",
                "description": "Période de répétition en secondes (ex: 3600 pour toutes les heures), requis si schedule_type='interval'.",
            },
            "task_id": {
                "type": "integer",
                "description": "Identifiant de la tâche à annuler (requis pour action='cancel', voir action='list').",
            },
        },
        ["action"],
    ),
]

# Memory tools: declared only if the matching memory is enabled (see core/settings.py).
if settings.MEM0_ENABLED:
    TOOL_DEFS.append(
        ToolDef(
            user_memory,
            "Mémoire RAPIDE sur l'utilisateur (mem0) : petits faits personnels et préférences durables "
            "(prénom, proches, ville, travail, goûts, habitudes, façon de répondre souhaitée...). Les faits "
            "pertinents sont DÉJÀ ajoutés automatiquement à son message dans un bloc « Mémoire rapide » : "
            "n'appelle cet outil que pour 'add' (retenir explicitement un fait quand l'utilisateur le "
            "demande : « retiens que... »), 'search' (chercher un fait absent du bloc), 'list' (dire tout ce "
            "que tu sais de lui) ou 'delete' (oublier un fait : « oublie que... » ; fais d'abord 'search' ou "
            "'list' pour obtenir son memory_id). Pour des projets, des décisions, des événements ou des "
            "échanges passés, utilise deep_memory.",
            {
                "action": {
                    "type": "string",
                    "enum": ["add", "search", "list", "delete"],
                    "description": "'add' retient un fait, 'search' cherche, 'list' liste tout, 'delete' oublie un fait.",
                },
                "text": {
                    "type": "string",
                    "description": "Pour 'add' : le fait à retenir, rédigé de façon complète et autonome "
                    "(ex: \"Ma sœur s'appelle Léa et habite à Lyon\"). Pour 'search' : ce qu'on cherche.",
                },
                "memory_id": {
                    "type": "string",
                    "description": "Identifiant du souvenir à oublier (requis pour 'delete'), tel qu'affiché par 'search' ou 'list'.",
                },
            },
            ["action"],
        )
    )

if settings.HINDSIGHT_ENABLED:
    TOOL_DEFS.append(
        ToolDef(
            deep_memory,
            "Mémoire LONG TERME (Hindsight) : projets de l'utilisateur et leur avancement, décisions et leurs "
            "raisons, événements, conversations passées, enseignements tirés. 'reflect' (à privilégier) "
            "répond à une question en raisonnant sur l'ensemble des souvenirs (ex: « où en est-on sur "
            "l'avatar 3D ? », « qu'avons-nous décidé pour... ? », « que sais-tu de ma façon de travailler ? ») ; "
            "'recall' renvoie les souvenirs bruts les plus pertinents (pour vérifier un détail précis ou une "
            "date) ; 'retain' enregistre explicitement une information importante à ne pas perdre (décision, "
            "conclusion, résultat). Les conversations sont déjà mémorisées automatiquement en arrière-plan : "
            "n'utilise 'retain' que pour consigner quelque chose d'explicite.",
            {
                "action": {
                    "type": "string",
                    "enum": ["reflect", "recall", "retain"],
                    "description": "'reflect' répond à une question à partir des souvenirs, 'recall' renvoie les "
                    "souvenirs bruts, 'retain' en enregistre un.",
                },
                "query": {
                    "type": "string",
                    "description": "Pour 'reflect' et 'recall' : la question ou le sujet, formulé de façon "
                    "complète (ex: \"Où en est le projet d'avatar 3D de Monika ?\").",
                },
                "content": {
                    "type": "string",
                    "description": "Pour 'retain' : l'information à enregistrer, rédigée de façon autonome "
                    "(sujet, décision, raison, date si utile).",
                },
            },
            ["action"],
        )
    )

if settings.RAG_ENABLED:
    TOOL_DEFS.append(
        ToolDef(
            search_documents,
            "Recherche dans les DOCUMENTS PERSONNELS de l'utilisateur (notes, PDF, Word, code, textes de son dossier "
            "personnel) par le sens ET par mots-clés, avec classement par pertinence. À utiliser pour retrouver ce que "
            "dit un de SES fichiers quand le chemin est inconnu, ou pour répondre à une question sur le contenu de ses "
            "documents. Renvoie des extraits avec chemin et position (page, section, lignes) ; pour lire un fichier en "
            "entier, utilise ensuite read_file. Pas pour le web (web_search/fetch_url), pas pour les souvenirs de "
            "conversation (deep_memory/user_memory), pas pour chercher un nom de fichier (glob).",
            {
                "query": {
                    "type": "string",
                    "description": "Ce que tu cherches, avec les mots probables du document (ex: \"contrat de bail "
                    "clause de résiliation préavis\"), pas la phrase de l'utilisateur telle quelle.",
                },
                "path": {
                    "type": "string",
                    "description": "Optionnel : restreint la recherche à ce dossier (ou fichier), ex: ~/Documents/Obsidian.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Nombre d'extraits à renvoyer (1 à 10, défaut 5).",
                },
            },
            ["query"],
        )
    )

if settings.BACKGROUND_ENABLED:
    TOOL_DEFS.append(
        ToolDef(
            background_task,
            "Confie une tâche LONGUE à un sous-agent qui travaille en arrière-plan pendant que tu continues à "
            "discuter avec l'utilisateur, qui est prévenu à la fin. 'start' lance la tâche (retour immédiat) ; "
            "'status' donne l'état et le résultat d'une tâche ; 'list' liste les tâches ; 'cancel' en annule une. "
            "À utiliser pour ce qui prendrait plusieurs minutes (recherche approfondie sur plusieurs sources, "
            "génération d'un projet, analyse de nombreux fichiers) ou quand l'utilisateur dit de le faire « en "
            "fond » ; PAS pour une action rapide (quelques outils), à faire directement. Le sous-agent n'a pas le "
            "navigateur (browser_control) et ne peut pas poser de questions : la demande doit être complète.",
            {
                "action": {
                    "type": "string",
                    "enum": ["start", "status", "list", "cancel"],
                    "description": "L'action à effectuer.",
                },
                "goal": {
                    "type": "string",
                    "description": "Pour 'start' : la tâche, rédigée de façon complète et autonome (objectif, "
                    "contraintes, où écrire le résultat si besoin), car le sous-agent ne voit pas la conversation.",
                },
                "job_id": {
                    "type": "integer",
                    "description": "Pour 'status' et 'cancel' : le numéro de la tâche (donné au lancement ou par 'list').",
                },
            },
            ["action"],
        )
    )

# Per-tool flags (see ToolDef). Gathered here to see them at a glance; a tool absent from this table
# (e.g. a custom tool) has no flag.
_TOOL_FLAGS: dict[str, dict[str, Any]] = {
    "browser_control": {
        "lent": True,
        "phrase_attente": (
            "Je m'en occupe sur le web, ça peut prendre un petit moment.",
            "Je navigue, laisse-moi quelques secondes.",
        ),
        "non_fiable": True,
    },
    "web_search": {
        "lent": True,
        "phrase_attente": ("Je cherche ça sur le web.", "Je regarde ce qu'on trouve sur le sujet."),
        "non_fiable": True,
    },
    "fetch_url": {"lent": True, "phrase_attente": "Je lis la page.", "non_fiable": True},
    "run_script": {"lent": True, "phrase_attente": "Je lance la commande, un instant."},
    "search_documents": {"lent": True, "phrase_attente": "Je cherche dans tes documents."},
    "deep_memory": {"lent": True, "phrase_attente": "Je fouille dans mes souvenirs, une seconde."},
    "analyze_image": {"lent": True, "phrase_attente": "Je regarde l'image de plus près."},
    "create_full_project": {"lent": True, "phrase_attente": "Je prépare le projet, ça prend quelques secondes."},
    "email_control": {"lent": True, "phrase_attente": "Je regarde ta boîte mail."},
    "calendar_control": {"lent": True, "phrase_attente": "Je consulte ton agenda."},
}

for _tool in TOOL_DEFS:
    for _flag, _value in _TOOL_FLAGS.get(_tool.name, {}).items():
        setattr(_tool, _flag, _value)

if settings.DISCORD_CALLS_ENABLED:
    TOOL_DEFS.append(
        ToolDef(
            discord_call,
            "Appel vocal Discord avec l'utilisateur. 'call_me' : l'appeler (elle rejoint son salon vocal, ou lui envoie un "
            "message privé avec le lien du salon et l'attend) ; à utiliser quand il demande « appelle-moi » ou quand tu "
            "dois vraiment le joindre. 'hangup' : raccrocher. 'status' : état de l'appel.",
            {
                "action": {
                    "type": "string",
                    "enum": ["call_me", "hangup", "status"],
                    "description": "L'action à effectuer.",
                },
                "message": {
                    "type": "string",
                    "description": "Pour 'call_me' : ce que Monika dit en premier quand l'utilisateur répond (facultatif).",
                },
            },
            ["action"],
        )
    )

AVAILABLE_TOOLS: dict[str, Callable[..., Any]] = {t.name: t.func for t in TOOL_DEFS}
TOOLS_SCHEMA: list[dict] = [t.to_schema() for t in TOOL_DEFS]
# To find a tool's flags from its name (used by agents/runtime.py).
TOOL_DEFS_BY_NAME: dict[str, ToolDef] = {t.name: t for t in TOOL_DEFS}


def _generate_schema_from_func(func, description: str) -> dict:
    """Generate a JSON schema from a function's signature (used for custom tools, which have no explicit schema)."""
    sig = inspect.signature(func)
    type_map = {int: "integer", float: "number", bool: "boolean"}
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        properties[param_name] = {
            "type": type_map.get(param.annotation, "string"),
            "description": f"Paramètre {param_name}",
        }
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return ToolDef(func, description, properties, required).to_schema()


def sync_custom_tools(target_schema_list: list) -> None:
    """Load the custom tools of tools/custom/ into AVAILABLE_TOOLS and add their JSON schema to `target_schema_list`."""
    custom_dir = os.path.join(os.path.dirname(__file__), "custom")
    if not os.path.exists(custom_dir):
        return

    if custom_dir not in sys.path:
        sys.path.append(custom_dir)

    for filename in os.listdir(custom_dir):
        if not filename.endswith(".py") or filename.startswith("__"):
            continue

        tool_name = filename[:-3]
        try:
            module_name = f"tools.custom.{tool_name}"
            module = (
                importlib.reload(sys.modules[module_name])
                if module_name in sys.modules
                else importlib.import_module(module_name)
            )

            func = getattr(module, tool_name, None)
            if func is None:
                continue

            AVAILABLE_TOOLS[tool_name] = func

            if not any(s["function"]["name"] == tool_name for s in target_schema_list):
                doc = (func.__doc__ or f"Outil personnalisé {tool_name}").strip()
                target_schema_list.append(_generate_schema_from_func(func, doc))
                print(f"🧩 [Outil Personnalisé Chargé] : {tool_name}")
        except Exception as e:
            print(f"⚠️ Impossible de charger l'outil personnalisé {tool_name} : {e}")


sync_custom_tools(TOOLS_SCHEMA)