# Commandes

## Terminal

| Commande | Effet |
|---|---|
| `python -m monika` | Démarre Monika en local : la conversation se passe dans la page (le processus vit tant qu'elle est ouverte) |
| `./lancer-monika.sh` | Démarre la voix GPT-SoVITS si besoin, vérifie qu'elle répond, puis lance Monika (mêmes options que ci-dessous) |
| `python -m monika --mode voix` | Conversation à voix haute (réponses aussi à l'écran) |
| `python -m monika --config` | Affiche la configuration chargée (secrets masqués) |
| `python -m monika --roles` | Les rôles du modèle et le modèle que chacun utilise |
| `python -m monika --telephone` | La commande `tailscale serve` et le lien HTTPS à ouvrir depuis le téléphone |
| `python -m monika --tools` | Liste les serveurs MCP et leurs outils |
| `python -m monika --call NOM '{"arg": 1}'` | Appelle un outil à la main |
| `python -m monika --rag-index [DOSSIER ...]` | Indexe tes documents (`--reset` : repart d'un index vide) |
| `python -m monika --rag-search MOT ...` | Cherche dans l'index, avec citations |
| `python -m monika --rag-status` | État de l'index de documents |

## Dans le chat

`/help` les rappelle ; les anciens noms français restent acceptés.

| Commande | Effet |
|---|---|
| `/tools` (`/outils`) | Liste des outils disponibles |
| `/context` (`/contexte`) | Taille estimée de la conversation, répartie entre prompt, outils (par source, avec les plus lourds) et historique |
| `/memory` (`/memoire`), `/forget ID` (`/oublier ID`) | Ce que Monika sait de toi ; supprime un souvenir |
| `/relationship` (`/relation`) | Où en est votre relation (niveau, favorabilité, humeur) ; `/relationship reset` (`reinitialiser`) repart de zéro |
| `/journal` | Les notes que Monika a écrites sur toi ; `/journal forget ID` en supprime une, `/journal clear` tout |
| `/nightly` (`/journal-nuit`) | La note de la nuit dans Obsidian ; `/nightly show DATE`, `delete DATE`, `accept ID`, `refuse ID` |
| `/sessions` | Les sessions enregistrées, les plus récentes d'abord |
| `/resume ID` (`/reprendre ID`) | Reprend une session : son historique et son résumé sont rechargés |
| `/search WORD …` (`/chercher`) | Cherche dans les tours enregistrés (plein texte) |
| `/session forget ID` (`oublier`) | Supprime une session, ses tours et ses appels d'outils |
| `/index [folder]` (`/indexer`), `/documents` | Lance l'indexation en arrière-plan ; état de l'index |
| `/remind WHEN : TEXT` (`/rappel`) | Programme un rappel (« /remind in 20 minutes: drink water ») |
| `/tasks` (`/taches`), `/task cancel ID` (`/tache annuler ID`) | Les tâches planifiées ; en supprime une |
| `/briefing` | Le briefing du matin tout de suite, sans attendre l'heure dite |
| `/custom-tools` (`/outils-perso`) | Les serveurs MCP que Monika a écrits : liste, `code NAME` (relire le code et le test), `disable NAME`, `delete NAME` |
| `/recipes` (`/recettes`) | Mes procédures apprises : liste ; `keep ID`, `refuse ID`, `forget ID` |
| `/stop` | Arrête tout ce qui tourne (réponse, voix, indexation) et dit quoi |
| `/clear` (`/effacer`) | Oublie la conversation en cours (et son résumé) |
| `/quit` (`/quitter`, `/q`, Ctrl+D) | Quitte |

Ctrl+C interrompt la réponse en cours **et** arrête ce qui tourne en arrière-plan (voix,
indexation) ; les captures mémoire déjà lancées vont jusqu'au bout.
