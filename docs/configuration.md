# Configuration et structure du projet

## Configuration

Tous les réglages sont dans `.env` (modèle commenté : `.env.example`, qui reprend exactement les
valeurs par défaut du code). Les plus utiles :

| Réglage | Rôle |
|---|---|
| `LANGUAGE` | Langue de l'interface, des prompts et des lexiques : `fr` (défaut) ou `en` |
| `LLM_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME` | Fournisseur et modèle (API compatible OpenAI) |
| `REFLECTION_MODEL` | Modèle du rôle réflexion (mémoire, résumés, veille) ; vide = `MODEL_NAME` |
| `SURFACE_LLM_MODEL`, `SURFACE_PROMPT`, `SURFACE_TIMEOUT_S` | Rôle surface : réplique immédiate avant les outils (vide = désactivé) |
| `REFLECTION_ENABLED`, `REFLECTION_SPEAK`, `REFLECTION_PROMPT` | Conseil préparé entre les tours et prise de parole spontanée (désactivés par défaut) |
| `MONIKA_HOME` | Dossier des données (défaut `~/.config/monika`) |
| `USER_NAME` | Ton prénom, utilisé dans le prompt système |
| `PERSONA_TEXT` | Consignes libres sur son comportement, ajoutées à son prompt (page de réglages) |
| `MAX_STEPS` | Appels au modèle par tour (défaut 100) |
| `ACTION_LLM_ENABLED`, `ACTION_LLM_MODEL` | Rôle action : chaîne de gestes de l'avatar après la réponse (désactivé par défaut) |
| `CONTEXT_BUDGET_TOKENS` | Seuil de résumé automatique (défaut 150 000) |
| `SESSIONS_ENABLED`, `SESSIONS_KEEP_DAYS` | Historique des conversations sur disque (désactivé par défaut ; rétention 30 jours) |
| `TASKS_ENABLED`, `TASKS_MAX` | Rappels et tâches récurrentes (désactivé par défaut, 50 tâches max) |
| `BRIEFING_ENABLED`, `BRIEFING_TIME`, `BRIEFING_DAYS` | Briefing du matin : météo, agenda, mails et tâches du jour (désactivé par défaut, 07:30) |
| `BRIEFING_CITY`, `BRIEFING_SECTIONS` | Ville de la météo (Open-Meteo, sans clé) et sections demandées |
| `FAST_MEMORY_ENABLED`, `MEMORY_CAPTURE_ENABLED` | Carnet de notes et capture automatique |
| `RAG_ENABLED`, `RAG_ROOTS`, `RAG_EXCLUDE`, `RAG_MIN_SCORE` | Documents personnels (dont le seuil de pertinence) |
| `STT_MODEL`, `TTS_ENABLED` | Voix |
| `TTS_URL`, `TTS_SPEAKERS_DIR`, `TTS_CHARACTER_ID` | GPT-SoVITS : adresse de l'API et pack de voix |
| `TTS_LANGUAGE`, `TTS_EMOTION_TAGS`, `TTS_RENDER` | Langue parlée, tags d'émotion, découpage des requêtes |
| `TTS_PAUSE_STYLE`, `TTS_PAUSE_SCALE`, `TTS_PAUSE_MAX_S` | Pauses entre phrases : `neutre` (rien) ou `naturel` |
| `WEB_ENABLED`, `WEB_PORT`, `WEB_HOST` | Page locale (défaut 8765, sur `127.0.0.1` seulement) |
| `WEB_TOKEN` | Jeton d'accès à la page (vide = aucun) ; à définir dès que `WEB_HOST` n'est plus local |
| `SENSITIVE_WORDS_ENABLED` | Filtre de mots sensibles (la liste est dans `words.json`) |
| `RECIPES_ENABLED`, `RECIPES_MIN_TOOLS`, `RECIPES_INJECT_MAX`, `RECIPES_MAX` | Recettes apprises : proposition après un tour réussi, outils minimum, recettes injectées par tour, total |
| `LOG_LEVEL`, `AUDIT_ENABLED` | Journaux |

Une valeur invalide est refusée au lancement avec un message clair (par exemple
`LOOP_STOP_AFTER` doit dépasser `LOOP_WARN_AFTER`, et il faut
`CONTEXT_KEEP_RECENT_TOKENS` < `CONTEXT_BUDGET_TOKENS` < `CONTEXT_WINDOW_TOKENS`).

## Structure du projet

```
monika/        Point d'entrée (python -m monika), configuration (config.py), chemins (paths.py), embeddings.py,
               verrou d'instance (lock.py), lien du téléphone (phone.py)
agent/         Boucle ReAct (loop.py), client LLM (llm.py), contexte (context.py), prompt (prompt.py),
               sessions persistantes (sessions.py), tâches planifiées (tasks.py), détection de boucle,
               phrases d'attente, infos système
tools/         Registre d'outils audité (registry.py), client MCP (mcp_client.py), outils internes (internal.py)
security/      Masquage des secrets (masking.py), enveloppe « données, pas ordres » (untrusted.py), audit (audit.py)
bridges/       Pont MCP autonome vers un helper HTTP (Hermes, nanoMuse) : ask, status, answer, stop, health
memory/        Carnet de notes personnelles (notes.py) et capture en arrière-plan (capture.py)
rag/           Documents personnels : scan, lecture, découpage, index, recherche hybride, reranking
character/     Émotions, relation (favorabilité), journal, gestes d'avatar
voice/         Micro, détection de parole, Whisper, GPT-SoVITS (client HTTP), lecture audio, nettoyage
               du texte parlé
modes/         Tour de conversation et commandes (text.py), lecteur vocal (voice.py)
web/           Serveur local (server.py), tableau de bord (dashboard.py), diffusion en direct (hub.py),
               conversation de la page (chat.py), voix de la page (voice.py) et page (static/ :
               index.html, css/, js/, avatar/, clips/, vendor/)
```

Les paquets (`agent/`, `rag/`, `web/`…) sont à la racine du projet : lance toujours Monika
**depuis ce dossier**, en `python -m monika`.
