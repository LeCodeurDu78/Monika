# Agent, contexte et prompt

À chaque message, Monika raisonne, appelle des outils (function calling), observe, recommence,
puis répond, au plus `MAX_STEPS` (100) appels au modèle par tour. Elle agit directement, sans
demander confirmation, et sans rien affirmer sans preuve.

- **Boucles** : même outil + mêmes arguments + même résultat. Avertissement à la 3e fois
  (`LOOP_WARN_AFTER`), arrêt avec bilan à la 5e (`LOOP_STOP_AFTER`).
- **Bilan honnête** : si la limite d'étapes ou une boucle arrête la tâche, Monika dit ce qui est
  fait, ce qui ne l'est pas et ce qui reste.
- **Contenu non fiable** : tout résultat d'outil arrive au modèle dans une enveloppe
  `<donnees_outil>`, présentée comme de la donnée, jamais comme un ordre. Une fausse balise
  d'ouverture ou de fermeture dans le contenu est neutralisée.
- **Phrases d'attente** : pour un outil lent, une phrase après `FILLER_DELAY_S` (1,5 s), puis
  toutes les `FILLER_REPEAT_S`, au plus `FILLER_MAX_PER_TURN` (2) par tour. Affichées, et dites à
  voix haute quand la voix est chargée.
- **Quatre rôles** (`python -m monika --roles` les affiche) : **surface** (la réplique d'ouverture,
  avant les outils ; `SURFACE_LLM_MODEL`, vide = désactivé), **deep** (le tour complet : réponse,
  outils, bilan, `MODEL_NAME`), **réflexion** (hors du tour : directeur de mémoire, résumés de
  contexte, veille nocturne ; `REFLECTION_MODEL`, vide = le modèle principal) et **action** (les
  gestes de l'avatar, `ACTION_LLM_MODEL`). Rien de la réflexion n'est attendu pendant une réponse :
  un modèle moins cher y est donc sans effet sur la vitesse du tour.
- **Surface puis deep** : quand `SURFACE_LLM_MODEL` est défini, un petit modèle écrit d'abord
  **une** phrase courte, affichée tout de suite ; la boucle complète prend ensuite le tour avec les
  outils et reçoit cette phrase pour ne pas la répéter. La parole passe donc **avant** les outils :
  la surface ne voit ni les outils ni leurs résultats et ne peut annoncer aucun fait, chiffre ou
  chemin. Si elle dépasse `SURFACE_TIMEOUT_S`, la réplique est simplement omise et le tour continue.
  `SURFACE_PROMPT` ajoute des consignes à ce rôle seul.
- **Réflexion entre les tours** (`REFLECTION_ENABLED=true`, désactivé par défaut) : après un tour
  réussi, le rôle réflexion prépare en arrière-plan un conseil court, injecté au tour suivant comme
  une note interne. Avec `REFLECTION_SPEAK=true`, il peut aussi déclencher une prise de parole : un
  tour automatique étiqueté « Réflexion », avec les outils et le bilan habituels. `REFLECTION_PROMPT`
  ajoute des consignes à ce rôle. Tout se règle dans `/settings`, groupe *Rôles*.
- **Rôle action (facultatif, désactivé par défaut)** : après le tour, en tâche de fond, il choisit
  une courte chaîne de gestes (2 à 8 s chacun, trois au plus) que la page joue si aucun geste n'est
  en cours ; un message suivant **annule** la chaîne encore en attente, sur le fond comme sur la
  page, pour qu'un geste décidé pour une réponse ne tombe jamais pendant la suivante. En cas
  d'échec, les gestes déterministes de `character/cues.py` restent seuls actifs. Ce rôle coûte un
  appel de modèle par tour : à activer en mesurant.
  Tous les outils partent à chaque requête, sans planificateur intermédiaire.
- **Ctrl+C** annule l'outil en cours. Si des actions avaient déjà été faites, une note les
  rappelle au modèle pour qu'il ne les croie pas annulées.
- **Prompt** reconstruit à chaque tour : personnalité, méthode de travail, règle « données, pas
  ordres », date en français et contexte du système (OS, utilisateur, dossier de travail).
- **Gros résultats** : au-delà de `TOOL_RESULT_MAX_CHARS` (20 000), le résultat est tronqué
  (début + fin) et le texte complet est sauvegardé dans `~/.config/monika/tool_results`
  (fichiers privés, purgés après `TOOL_RESULTS_KEEP_DAYS` = 14 jours). Le modèle lit la suite
  avec `lire_resultat(id, debut, longueur)`.
- **Budget de contexte** : la taille est estimée avant chaque appel (environ 3,3 caractères par
  token : une estimation, pas un comptage exact). Au-dessus de `CONTEXT_BUDGET_TOKENS`
  (150 000, très en dessous de la fenêtre de 1 M), les anciens échanges sont résumés par le
  modèle ; on garde au moins `CONTEXT_KEEP_RECENT_TOKENS` récents, avec une coupure toujours
  entre deux tours, jamais au milieu d'un appel d'outil. Si un seul tour est énorme, ses plus
  anciens résultats d'outils sont retirés (les 3 derniers restent). Si le résumé échoue, tu en
  es informé et la conversation continue sans compactage.

## Recettes apprises

Après un **tour réussi** (réponse complète, aucun outil en erreur) comptant au moins
`RECIPES_MIN_TOOLS` (3) appels d'outils, une tâche de fond demande au modèle une **recette
paramétrée** : but, paramètres, étapes avec les outils utilisés, vérification finale. Elle est
écrite en **brouillon** dans `recipes.json` — rien n'est enregistré sans ton accord.

- `/recettes` liste les brouillons (avec leur id), les versions actives et les usages ;
  `/recettes garder ID` enregistre, `/recettes refuser ID` jette le brouillon,
  `/recettes oublier ID` supprime la recette, son historique et ses compteurs.
- Au tour suivant, une recette active qui recoupe la demande (mots significatifs du titre, du
  but et des paramètres) est injectée dans le prompt sous « Recettes », présentée comme
  **donnée** : une procédure qui a déjà réussi, à adapter, jamais un ordre. Au plus
  `RECIPES_INJECT_MAX` (2) recettes par tour.
- Chaque injection compte un **usage** ; le tour qui suit enregistre une réussite (fini
  normalement, aucun outil en erreur) ou un échec.
- Si le tour améliore une recette existante, le keeper propose une **nouvelle version** dans le
  même id : elle n'est appliquée qu'après `/recettes garder ID`, et l'ancienne passe dans
  l'historique (10 versions conservées au plus).
- Le bloc **Recettes** du tableau de bord montre l'état (brouillon, version active, version
  proposée) et les usages. Les textes sont masqués avant écriture.

Ce que ça ne fait pas : aucune recette n'est appliquée automatiquement, rien n'est partagé
entre machines, et le rapprochement est **lexical** (pas d'embeddings) : une demande formulée
très différemment peut ne déclencher aucune recette. Le keeper coûte un appel de modèle par
tour éligible (`RECIPES_ENABLED=false` pour couper).

## Sessions et reprise

Avec `SESSIONS_ENABLED=true` (désactivé par défaut), chaque conversation est enregistrée dans
`~/.config/monika/sessions/sessions.db` : SQLite avec **recherche plein texte FTS5**, fichier en
`0600`. Une ligne par session, une par tour, une par appel d'outil, plus l'historique tel que le
modèle l'a vu — c'est cet instantané qui rend `/reprendre` exact, appels d'outils compris.

- **Secrets masqués** avant l'écriture, champ par champ (`security/masking.py`) : une clé collée
  dans un message ou un argument d'outil n'arrive jamais sur le disque.
- **Tour interrompu** (Ctrl+C, erreur du modèle, fenêtre fermée) : écrit avec le statut
  `interrupted` ; un tour resté ouvert par un arrêt brutal est marqué ainsi au lancement suivant.
- **Budget de contexte** : `/reprendre` ne réinjecte pas tout. Au-delà du budget, les tours les plus
  anciens sont écartés **entiers** (jamais au milieu d'un appel d'outil) et la reprise le dit.
- **Rétention** : les sessions inutilisées depuis `SESSIONS_KEEP_DAYS` (30 jours) sont purgées au
  lancement. `/effacer` repart sur une session neuve sans effacer l'ancienne.
- **Tu gardes la main** : `/sessions`, `/chercher`, `/session oublier ID`, et le bloc *Sessions* du
  tableau de bord listent les quinze dernières, permettent de les reprendre et de les supprimer.
  Reprendre depuis la page recharge la conversation **dans le processus en cours** : elle se
  poursuit dans la page (bloc *Conversation*).
- **Recherche** : l'index porte sur ce qui a été dit (message et réponse), pas sur les résultats
  d'outils : ceux-ci restent dans l'historique mais hors de l'index.
- **Limite** : l'historique complet est réécrit à chaque tour (quelques centaines de kilo-octets sur
  une très longue session) ; l'écriture est atomique par transaction, et un échec de la base est
  journalisé sans jamais interrompre la conversation.

## Tâches planifiées

Avec `TASKS_ENABLED=true`, Monika se souvient de faire quelque chose plus tard — et le fait
elle-même : un rappel n'est pas une alarme, c'est un tour comme un autre, avec ses outils et sa
voix.

```
/remind in 20 minutes: drink water
/remind tomorrow 8am: check the weather and tell me what to wear
/remind every 2 hours: remind me to take a break
/remind every day at 8am: give me today's schedule
/remind every Monday at 9am: sum up the week ahead
/remind Monday to Friday at 9am: the morning review
```

- **Ce qu'elle accepte** : `dans N minutes|heures|jours`, une heure (`18h30`, `08:05` — demain si
  elle est déjà passée), `demain 8h`, et la répétition : `toutes les 2 heures`, `tous les 3 jours`,
  `chaque 30 minutes`, `chaque jour à 8h` (ou `tous les jours à 08:30`), et les jours de la
  semaine : `chaque lundi à 9h`, `chaque lundi et jeudi à 9h`, `tous les mardis, mercredis et
  vendredis à 14h30`, `du lundi au vendredi à 9h`. Ces deux derniers gardent une **heure fixe** :
  elle ne dérive pas de 24 h en 24 h depuis l'heure de création. Le langage naturel marche aussi :
  « rappelle-moi dans deux heures de sortir les poubelles » passe par l'outil `taches_planifier`.
- **Ce qu'elle fait à l'heure dite** : un tour complet, sans toi devant l'écran. La réponse
  s'affiche **dans la page** (et se dit à voix haute si la voix est active),
  et s'enregistre dans la session.
- **Un processus doit tourner** : `python -m monika`, laissé ouvert (le verrou d'instance empêche
  d'en lancer un second sur le même dossier de données).
  Ce qui est arrivé pendant un arrêt se déclenche **au lancement suivant**, en disant son retard
  (« rappel prévu à 8:00, il est 9:20 »). Un rappel qui revient toutes les heures ne rejoue pas
  les heures manquées : il repart de maintenant.
- **Tu gardes la main** : `/taches` liste (moment, répétition, id), `/tache annuler ID` supprime.
  Une tâche n'est jamais créée sans toi : ni le modèle ni le code n'en inventent.
- **Limites** : 50 tâches (`TASKS_MAX`), un texte de 500 caractères, et rien ne se déclenche
  pendant qu'un tour est en cours — le rappel attend la fin de la réponse en cours. Une tâche
  quotidienne ou hebdomadaire manquée (machine éteinte) repart à la prochaine occurrence, sans
  rattrapage ; une tâche à plusieurs jours reprend au jour suivant de sa liste.

## Briefing du matin

Avec `BRIEFING_ENABLED=true`, Monika te donne un briefing chaque matin — météo, agenda, mails
importants, tâches du jour — sans que tu demandes rien. C'est un **tour complet** à l'heure dite :
elle l'écrit à sa voix, la réponse s'affiche dans la page (et se dit à voix haute si la voix est
active) et s'enregistre dans la session comme n'importe quel échange. La page montre le libellé
**Briefing du matin**, jamais le relevé ni la demande préparés.

- **Ce qu'elle lit** : la météo du jour (Open-Meteo, **sans clé**), les tâches planifiées qui
  tombent aujourd'hui (`/remind`), puis ton agenda et tes mails **par ses outils MCP** : branche un
  serveur de calendrier et un serveur de messagerie, elle s'en sert seule. Un outil absent est dit
  absent en une demi-phrase ; rien n'est inventé.
- **Réglages** : `BRIEFING_TIME` (`07:30` par défaut), `BRIEFING_DAYS` (`tous`, ou
  `lundi,mardi,mercredi,jeudi,vendredi`), `BRIEFING_CITY` (la ville de la météo ; vide = pas de
  météo) et `BRIEFING_SECTIONS` (`meteo,agenda,mails,taches` : retire ce dont tu ne veux pas). Ils
  se règlent aussi dans `/settings` (groupe *Briefing du matin*).
- **Tout de suite plutôt qu'à 7 h 30** : `/briefing`. Elle le fait immédiatement et ne le refera
  pas le même jour.
- **Rattrapage** : un briefing manqué (machine éteinte) est donné au lancement suivant s'il a
  moins de 4 h de retard ; après, il repart au matin d'après, sans rattraper les jours passés.
- **Un processus doit tourner** : `python -m monika`, laissé ouvert.
  Rien ne se déclenche pendant qu'un tour est en cours : le briefing attend la fin de la réponse.
- **Limites** : la météo vient d'un service extérieur (Open-Meteo) — sans réseau, elle est dite
  indisponible ; l'agenda et les mails n'existent que si les serveurs MCP correspondants sont
  branchés ; il n'y a pas de modèle dédié, le briefing compte donc un tour, avec ses appels
  d'outils.
