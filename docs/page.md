# Avatar, tableau de bord et réglages

Au lancement, Monika ouvre une page à `http://localhost:8765` (`WEB_PORT` ; `WEB_ENABLED=false`
pour la couper). Deux **onglets** en haut : **Chat** (l'avatar et la conversation) et **Tableau de
bord** (contexte, relation, mémoire, sessions, recettes, outils perso) ; le choix est mémorisé
d'une visite à l'autre. Sur un écran large, l'avatar reste à gauche et la section choisie à
droite ; sur un téléphone, chaque section prend toute la largeur et l'avatar s'efface quand le
tableau de bord est ouvert. La **poignée** entre les deux se glisse pour donner plus de place au
panneau (la largeur est retenue), se règle au clavier (flèches gauche/droite) et revient au défaut
par double-clic ; son **bouton ❯ / ❮** range ou rouvre le panneau, comme dans OpenSakura.

- **Avatar** : un modèle VRM (`web/static/assets/avatar.vrm`) animé par des clips de mouvement
  (`web/static/assets/clips/`, manifeste `web/static/assets/clips.json`:
  repos, réflexion, parole, joie, surprise, hochement, salut,
  colère, pleurs, danse, mains sur les hanches, bras ouverts, index pointé, étirement… Le mouvement et le
  visage passent par le moteur d'**OpenSakura**, repris tel quel dans `web/static/js/avatar/animation.js` :
  même reciblage des clips, mêmes poids d'expressions (joie, colère, tristesse, détente, surprise), même
  clignement, même synchronisation labiale, pilotés par l'état et le son réel — aucun moteur maison par-dessus.
  Un voyant indique
  « Au repos », « J'écoute », « Je réfléchis » ou « Je parle ».
  Le décor est une salle de club littéraire au crépuscule, entièrement procédurale
  (`web/static/js/avatar.js`) : plancher en lattes, tableau noir, fenêtres à claies et
  rideaux, pupitres, suspension qui oscille et poussière en suspension. Aucun fichier à télécharger ;
  si le décor échoue à se construire, la scène reste utilisable sans lui.
- **Caméra** : glisser avec le bouton gauche tourne autour d'elle, la molette rapproche ou éloigne,
  et un glisser avec le bouton droit vers le haut ou le bas fait glisser le point de mire de sa tête  à ses chaussures (« `?view=full` » cadre le corps entier au départ). L'orbite ne passe jamais sous le
  sol, ni à travers le plafond ou les murs : en montant ou en dézoomant, la caméra s'arrête dans la
  pièce (l'angle le plus haut et la distance maximale dépendent alors de sa hauteur et du recul) ; elle
  suit sa respiration sans tremblement.
  **Bibliothèques 3D** : three.js et three-vrm sont fournis dans `web/static/vendor/` et servis
  localement (aucune dépendance à télécharger : le dossier est versionné). Ouvre la page avec `?debug`
  pour tester chaque clip et chaque émotion ; `?view=full` cadre le corps entier au lieu du buste.
- **Conversation** (premier bloc) : c'est là que tu parles à Monika. Un
  texte, Entrée envoie (Maj+Entrée passe à la ligne), **Arrêter** coupe la réponse en cours —
  exactement le même arrêt que `/stop`. Les rappels planifiés apparaissent marqués comme tels, et
  la réponse s'écrit dans la page au fur et à mesure. Vingt dernières lignes
  gardées en mémoire pour un rechargement (le durable, c'est le bloc *Sessions*) ; un tour lancé
  à voix haute (`--mode voix`) s'affiche aussi ici. Une seule conversation à la fois :
  envoyer pendant une réponse est refusé (`409`) plutôt que mis en file. Les **commandes**
  (`/help`, `/sessions`, `/resume ID`, `/remind …`, `/stop`…) s'y tapent aussi : leur réponse
  s'affiche dans la conversation.
- **Parler depuis la page** : le bouton **🎙 Parler** ouvre le micro du navigateur
  (`getUserMedia`, annulation d'écho et réduction de bruit demandées) et lit la réponse **ici**, au
  casque ou dans les haut-parleurs de l'appareil qui regarde la page. Voir
  [Parler à Monika dans le navigateur](voix.md#parler-à-monika-dans-le-navigateur).
- **Tableau de bord** (second onglet), sept blocs : *Conversation*, *Contexte* (taille de la
  conversation), *Relation* (niveau, favorabilité et humeur de Monika ; son journal, dont tu peux
  modifier ou supprimer chaque note, corriger ou effacer l'impression d'ensemble, ou tout effacer ;
  et « Repartir de zéro » pour la relation, avec confirmation), *Mémoire* (son carnet : notes,
  sujets en cours et épisodes passés, chacun modifiable ou supprimable) et *Sessions* (les quinze
  dernières conversations enregistrées, la session en cours signalée, avec **Reprendre** et
  **Supprimer**),
  *Recettes* (les procédures apprises : brouillon à garder, version active, usages) et *Outils
  perso* (les serveurs MCP que Monika s'est écrits : nom, état — actif, désactivé ou
  refusé —, empreinte du code et description ; un code modifié depuis la vérification y est
  signalé).
  Supprimer la session en cours repart sur une conversation neuve ; les deux actions sont refusées
  pendant une réponse, pour ne pas échanger l'historique sous les pieds d'un tour en cours.
- **Téléphone et accès distant** : la page est responsive (onglets collants, zones tactiles, marges
  de sécurité des encoches, défilement naturel sur mobile). Elle n'écoute que sur `127.0.0.1` :
  pour le téléphone, passe par Tailscale, voir [Depuis le téléphone](#depuis-le-téléphone-tailscale).
  Un autre appareil du réseau local demande `WEB_HOST=0.0.0.0` **et** `WEB_TOKEN` (sans jeton, la
  page est ouverte à qui atteint le port) ; voir
  [Accès à la page](securite.md#accès-à-la-page).
- **WebGL** est nécessaire dans le navigateur ; sans lui, la page l'indique et le tableau de bord
  marche quand même.

## Depuis le téléphone (Tailscale)

Monika reste sur `127.0.0.1` : `tailscale serve` ne proxyfie que `http://127.0.0.1`, termine le
HTTPS et parle à la page. Rien à ouvrir sur le réseau local, jeton inchangé (`WEB_TOKEN` dans `.env`).

```bash
python -m monika --telephone   # nom du tailnet, commande et lien à ouvrir
tailscale serve --bg 8765      # sur cette machine, une fois (Monika lancé)
```

Puis ouvre sur le téléphone (connecté au même tailnet) le lien affiché,
`https://<machine>.<tailnet>.ts.net/?token=<jeton>` : le jeton passe en cookie (HttpOnly, SameSite
strict, `Secure` en HTTPS) et l'adresse redevient propre. `tailscale serve status` vérifie la
configuration, `tailscale serve reset` la coupe. **Pas `tailscale funnel`** : ce serait tout
l'internet. Le HTTPS est aussi ce qui donne le micro au téléphone.

## Page de réglages (`/settings`)

Un lien « Réglages » dans l'en-tête ouvre cette page (même serveur). Elle a trois blocs :

- **Santé** : sept voyants — LLM, reconnaissance vocale, voix GPT-SoVITS, serveurs MCP, mémoire
  rapide, documents, page locale. Chaque sonde est bornée à 1,5 s et lancée en parallèle ; le
  rafraîchissement a lieu toutes les 5 s **uniquement si l'onglet est visible**, plus un bouton
  « Tester maintenant ». Un composant **désactivé** est gris, pas rouge ; un composant en panne est
  rouge avec le détail (par exemple « 127.0.0.1:9880 injoignable »). Le LLM est sondé par sa liste
  de modèles, jamais par une complétion (cela coûterait un appel) ; la voix par une connexion TCP.
- **Réglages** : une sélection lisible, groupée (modèle, comportement, personnage, rôles, mémoire,
  documents, voix, page). Les clés définies dans le `.env` sont **grisées et marquées** : le `.env`
  garde la priorité, la page ne fait pas semblant de les piloter, et l'API les **refuse** (400)
  même appelée à la main. « Enregistrer » valide puis écrit
  `<MONIKA_HOME>/settings.json` (en `0600`, écriture atomique) et **applique à chaud** ce qui peut
  l'être (température, `MAX_STEPS`, seuils, rôles, `PERSONA_TEXT`…) ; un bandeau liste les clés qui
  demandent un redémarrage (port, URL, modèles, appareils, index). **Tout ou rien** : une valeur hors
  bornes est refusée avec un message et n'écrit rien.
- **Clé API** : la page indique seulement « définie » ou « non définie » et n'affiche jamais la
  valeur. Enregistrer remplace la seule ligne `OPENAI_API_KEY` du `.env` du projet (une ligne, sans
  retour à la ligne, fichier en `0600`) ; redémarrage requis.

Ce que la page **ne peut pas** changer : `MONIKA_HOME`, les dossiers indexés (`RAG_ROOTS`), le
fichier des serveurs MCP et le dossier des voix — écrire un chemin depuis le navigateur ferait de la
page un lanceur de fichiers arbitraires. Ces clés restent éditables à la main dans `.env`.

Comme le reste de la page, `/api/settings` et `/api/health` s'ouvrent **sans jeton** (la première
contient la persona et les noms de modèles) ; les écritures passent par le même `act()` que le
tableau de bord. Si `settings.json` est
abîmé à la main **pendant** que Monika tourne, l'écrire depuis la page échoue avec un message clair :
corrige-le ou supprime-le. Au **démarrage**, un `settings.json` qui ne se charge pas (JSON cassé ou
valeur hors bornes) n'empêche plus rien : il est renommé en `settings.json.corrupt-<date>`, un
avertissement dit pourquoi, et Monika repart des valeurs par défaut.
