# Monika vs OpenAmika — comparaison fonctionnalité par fonctionnalité

> Document écrit le 9 novembre 2026, à partir du code et de la documentation des deux projets
> (`README.md`, `docs/`, `OpenAmika-main/README.md`, `OpenAmika-main/README_IF_YOU_ARE_AI_AGENT.md`,
> `OpenAmika-main/docs/`, `OpenAmika-main/vendor/tts-engine/README.md`).
>
> **Légende.** « Meilleure » signifie : meilleur compromis pour l'usage indiqué, pas supériorité absolue.
> `Monika` = le projet à la racine ; `OpenAmika` = le projet dans `OpenAmika-main/`.
> Les deux ne sont pas deux versions du même logiciel : Monika est une adaptation francophone
> personnelle qui reprend plusieurs briques d'OpenAmika et d'OpenSakura (mémoire-preuve,
> moteur d'avatar, mapping TTS, mots sensibles, directeur d'actions).

---

## 1. Résumé en dix lignes

| Question | Réponse courte |
|---|---|
| Le plus léger | **Monika** : un processus, ~4 Go de modèles, CPU possible |
| Le plus complet en voix/avatar | **OpenAmika** : Live2D, 142 motions, 69 émotions, barge-in |
| Le meilleur pour des documents personnels | **Monika** : RAG local avec citations |
| Le meilleur pour la domotique et la vision | **OpenAmika** : caméra Qwen3-VL, Zigbee/MQTT |
| Le meilleur pour la autonomie planifiée | **Monika** : rappels, briefing, recettes apprises |
| Le meilleur pour un GPU Windows « clé en main » | **OpenAmika** |
| Le meilleur pour un laptop sans GPU | **Monika** |
| Le plus facile à réutiliser dans un autre projet | **OpenAmika** : code MIT |
| Le plus facile à étendre par l'IA elle-même | **Monika** : `create_custom_tools` |
| Le plus sûr par défaut | **Monika** pour les données ; **OpenAmika** pour les approbations d'actions |

---

## 2. Identité, langues et licences

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Héroïne | Monika, inspirée de DDLC | Amika (Sakurai Mika), personnage original MekaHime | Selon l'usage |
| Univers | Club de littérature, 4ᵉ mur | Sanctuaire de Kyoto, colocataire arrivée en ville | Selon l'usage |
| Ton | Complice, direct, français | Anime, taquin, anglais/japonais/chinois | Selon l'usage |
| Émotions | 26, avec demi-vies et caps | 69 clés, chacune liée à une voix et un visage | **OpenAmika** |
| Relation | Favor 0–1, 4 niveaux, maturation par jours/messages | Blocs de relation et de style dans les scènes | **Monika** |
| Journal intime | Oui, notes, impression générale, sujets à reprendre | Non : mémoire factuelle seulement | **Monika** |
| Langue de l'interface | Français et anglais | Anglais, japonais, chinois | **Monika** pour un francophone |
| Langue des prompts | Français | Chinois (pensées internes incluses) | **Monika** pour un francophone |
| Voix française | Approximative : le français passe par le frontal anglais | Aucune : pas de français dans les frontaux | **Monika**, mais les deux sont limitées |
| Licence du code | Usage strictement personnel, pas de LICENSE open source | MIT | **OpenAmika** |
| Licence des assets | Personnelle (le VRM est fourni dans le dépôt) | Personnelle MekaHime, 18+, pas de redistribution ni d'entraînement | Égalité, les deux sont restrictives |
| Distribution | Projet local | GitHub public, releases, Hugging Face | **OpenAmika** |

**Meilleure : match nul, deux publics différents.** Monika est faite pour un utilisateur francophone
qui veut une assistante personnelle ; OpenAmika est une compagne anime complète, assumée 18+,
avec un écosystème public.

**Améliorations**

- Monika : proposer un pack de voix français natif (voir §6) ; clarifier la licence des assets
  (le VRM inclus vient d'un univers tiers) ; ajouter un `LICENSE` explicite même pour un usage privé.
- OpenAmika : documenter une voie « tout public » ou un mode sans contenu suggestif ; le README
  gagnerait un tableau des licences dès la première page, pas seulement dans `vendor/`.

---

## 3. Plateforme, installation et architecture

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Systèmes | Linux, macOS, Windows (Python 3.12+) | Windows 10/11 uniquement (Linux/macOS « untested ») | **Monika** |
| GPU | Aucun requis ; Whisper et embeddings sur CPU | NVIDIA RTX 20-series+, 8 Go VRAM (16 Go avec vision) | **Monika** |
| Disque | ~4 Go de modèles | ~45 Go, 15–20 Go de téléchargement | **Monika** |
| Durée d'installation | Quelques minutes | 30–60 min | **Monika** |
| Environnements Python | Un seul `.venv` | 4 envs (`brain`, `speech`, `embed`, `vision`) + venv TTS | **Monika** |
| Processus | Un seul : `python -m monika` | Launcher + speech, brain, embedder, vision, messengers, TTS | **Monika** pour la simplicité |
| Ports | 8765 (page), 9880 (TTS externe) | 53000, 5202, 8011, 8014, 5215, 8002 | **Monika** |
| Démarrage | Immédiat, page servie par le même processus | ~20 s pour le TTS, health-gated, ouverture quand speech est prêt | **Monika** |
| Diagnostic | `/settings` : 7 voyants, test à la demande | Lignes `OK`/`!!` du launcher, `/flow`, `/api/health` | **OpenAmika** |
| Configuration | `.env` prioritaire, `settings.json` pour le reste | `settings.json`, page Setup, wizard premier lancement | **OpenAmika** pour le guidage |
| Mise à jour | Manuelle | Manuelle aussi, mais scripts `--verify` et manifestes | **OpenAmika** |
| Conflits de ports | Un seul port applicatif, peu de risque | 8002 peut entrer en conflit avec OpenSakura | **Monika** |

**Meilleure : Monika pour la portabilité et la simplicité ; OpenAmika pour le diagnostic et
l'installation guidée sur une machine dédiée.**

**Améliorations**

- Monika : ajouter un `pyproject.toml`, un mode Docker et un vrai installateur (le README suffit
  aujourd'hui, mais un nouvel utilisateur doit encore créer son venv à la main) ; un wizard de
  premier lancement avec aperçu vocal, comme OpenAmika.
- OpenAmika : réduire les 5 processus et les 4 venvs (un venv partagé, vision et messengers
  chargés à la demande) ; tester réellement Linux ; détecter automatiquement un port TTS occupé
  au lieu de demander de éditer `settings.json`.

---

## 4. LLM, raisonnement et coûts

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Fournisseurs | Un endpoint compatible OpenAI (`LLM_BASE_URL`) | OpenRouter par défaut, Alibaba Model Studio, Ollama local | **OpenAmika** |
| Rôles | surface, deep, reflection, action | surface, deep, reflection, memory_write | **OpenAmika** (rôle mémoire séparé) |
| Configuration des rôles | `.env` + page Réglages, un modèle par rôle | `config/llm_roles.json`, presets, page Setup, hot-reload | **OpenAmika** |
| Suivi des coûts | Aucun | Ledger d'usage, `pricing.json`, règlement, cache | **OpenAmika** |
| Réflexe de surface | Une phrase avant les outils, si `SURFACE_LLM_MODEL` est défini | 4 lignes en ~0,5 s, puis deep en streaming | **OpenAmika** |
| Boucle d'outils | ReAct, 100 étapes, détection de boucles, revue honnête | Clerk post-parole, outils après la réponse | **Monika** pour les tâches longues |
| Streaming | Réponse écrite au fil de l'eau, voix par phrase | Deep en streaming, TTS par phrase, barge-in | **OpenAmika** pour la voix |
| Contexte | Résumé automatique à 150 k tokens | Prompt cache-first, historique + mémoire + scène | Égalité |
| Mode local | Possible via un endpoint Ollama, mais non documenté | Mode Fully Local documenté et testé | **OpenAmika** |
| Budget/plafond | Pas de plafond de coût | Pas de plafond par tour documenté non plus | Égalité |

**Meilleure : OpenAmika pour le confort de configuration et le temps réel ; Monika pour les
tâches agentiques longues et la sobriété.**

**Améliorations**

- Monika : presets Ollama/OpenRouter, compteur de tokens et de coût par tour, plafond
  quotidien ; documentation d'un mode 100 % local.
- OpenAmika : plafond de dépense par tour et par jour, alerte avant dépassement ; un mode
  « réponse d'abord, outils ensuite » déjà présent, mais à exposer clairement dans l'UI.

---

## 5. Voix : écoute, tour de parole, synthèse

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| STT | faster-whisper `small`, CPU, français, filtre anti-hallucinations | SenseVoiceSmall local GPU, partiels, multilingue | **Monika** en français ; **OpenAmika** en multilingue/temps réel |
| VAD | Silero via faster-whisper, 700 ms de silence | Silero VAD | Égalité |
| Tour de parole | Half-duplex : elle n'écoute pas pendant qu'elle parle | Barge-in : tu peux l'interrompre | **OpenAmika** |
| Réveil | Aucun mot-clé, écoute continue | Aucun mot-clé non plus | Égalité |
| TTS | GPT-SoVITS **de base**, `api_v2.py` sur 9880 | Serveur maison GPT-SoVITS v2ProPlus sur 8002 | **OpenAmika** (voir §6) |
| Français | Lu via le frontal anglais, donc accent et prosodie approximatifs | Non pris en charge | **Monika**, mais insuffisant |
| Émotions vocales | 26 émotions, 2 tags max par phrase, `auto` n'utilise que les clips existants | 69 tags, 45 mappés par défaut sur 67 ids V1 | **OpenAmika** |
| Pauses | `neutre` ou `naturel`, modulation par ponctuation et émotion | 0,25 s entre fragments, trim à 0,12 s | **Monika** pour le réglage |
| Latence | Non mesurée dans le dépôt ; moteur de base, une phrase par requête | 0,34–1,06 s par phrase sur RTX 5080, RTF 0,13–0,39 | **OpenAmika** |
| Échec TTS | Message clair, texte à l'écran, coupure après 3 échecs | Ligne de santé rouge, fonctionnement en sous-titres | **Monika** |
| Micro navigateur | Même pipeline que le mode voix, WebAudio, demi-duplex | WebSocket, barge-in, audio par phrase | **OpenAmika** |
| Interruption | Ctrl+C coupe la voix ; un nouveau message en mode texte aussi | Barge-in vocal | **OpenAmika** |

**Meilleure : OpenAmika pour l'expérience vocale temps réel ; Monika reste la seule à proposer
une voix compréhensible en français, au prix d'un accent.**

**Améliorations**

- Monika : backend TTS enfichable (Piper, XTTS-v2, F5-TTS) pour le français natif, ou
  fine-tuning GPT-SoVITS sur un corpus français ; ajouter le barge-in avec annulation d'écho ;
  adopter les correctifs du serveur d'OpenAmika (§6) ; mot d'activation optionnel.
- OpenAmika : supporter une troisième langue cible, dont le français, ou exposer un adaptateur
  TTS générique ; compléter le mapping 69→67 (45 cellules par défaut, donc des émotions tombent
  sur `neutral1`) ; enregistrer les clips manquants ou documenter les fallbacks.

---

## 6. GPT-SoVITS : le serveur d'OpenAmika vs GPT-SoVITS de base

C'est la question la plus technique, et elle explique une grande partie de l'écart vocal.

### 6.1 Ce qu'est « celui de base »

Le GPT-SoVITS de base est le dépôt **RVC-Boss/GPT-SoVITS**, ici épinglé au commit
`b4570b876f5c` et **non modifié**. On l'utilise par `api_v2.py` : on lui envoie du texte,
un `ref_audio_path` et un `prompt_text`, il renvoie un WAV à 32 kHz. Il ne connaît ni les
dossiers de voix, ni les tags d'émotion, ni le changement de locuteur par requête.

### 6.2 Ce qu'est « le sien »

`OpenAmika-main/vendor/tts-engine/main.py` est un **serveur MIT de MekaHime**, dérivé du
serveur vocal d'OpenSakura, qui tourne **sur le même checkout upstream non modifié**. Il ajoute
une couche de service et corrige quelques comportements d'inférence **à l'exécution**, en
recompilant deux méthodes upstream en mémoire, sans toucher aux fichiers sur disque.

Ce n'est donc **pas un nouveau modèle** : les poids GPT/SoVITS, l'architecture et la qualité
de voix sont ceux de GPT-SoVITS. C'est un **meilleur serveur d'inférence** pour un usage
compagnon temps réel.

### 6.3 Les différences, une par une

| # | Sujet | GPT-SoVITS de base | Serveur d'OpenAmika |
|---|---|---|---|
| 1 | Protocole | HTTP `api_v2.py` | Socket.IO + HTTP, contrat `tts_request_gateway` / `audio_ready` |
| 2 | Voix | Un seul locuteur chargé au démarrage | Dossiers `Speakers/<id>/`, changement de locuteur par requête |
| 3 | Émotions | Aucune notion de tag | `【happy2】` → clip de référence et transcript dédiés, fallback `neutral1`, changement de tag au milieu d'un texte |
| 4 | Cache de prompt | Recalcul à chaque requête | Un cache par clip de référence |
| 5 | Références longues | Refuse hors 3–10 s | Accepte jusqu'à 15 s (`--ref-max-s`) ; 23 des 69 clips d'Amika font 10–14,4 s |
| 6 | Références courtes | Refuse sous 3 s | Complétées par du silence dans `cache/` |
| 7 | En-tête des poids SoVITS | Un `.pth` dont le marqueur `05`/`06` a été remis à `PK` est deviné comme un v2 simple, puis échoue | Détecte le marqueur, restaure une copie correcte dans `cache/` |
| 8 | Découpage du texte | Un run entier, ou `cut1` toutes les 4 phrases | **Une phrase = un fragment GPT**, point de départ ajouté, décodage par lots de 4 |
| 9 | Phrases manquantes | 18 prises sur 125 avaient une phrase absente, souvent la première | **0 sur 125** ; temps par réponse divisé par deux |
| 10 | Fin de phrase (EOS) | La boucle CUDA-graph peut terminer après 1–2 tokens, donc sans parole | Plancher EOS porté à 11 tokens pour cette boucle |
| 11 | Phrases trop courtes | Aucun garde-fou | `dropout-guard` : si la parole fait moins de 55 % de la durée attendue, nouvelle prise avec un autre seed, 2 essais max |
| 12 | Silences | Jusqu'à 1,3 s d'attaque, 0,25 s de queue | 0,12 s avant/après, seuil à 45 dB sous la frame la plus forte |
| 13 | Format audio | WAV 16 bits 32 kHz | WAV ou Opus 48 kHz, découpage au-delà de 3 Mio, rééchantillonnage |
| 14 | File d'attente | Synthèse synchrone | Une requête à la fois, worker dédié, drop propre des requêtes d'un client déconnecté |
| 15 | Santé et mesure | Rien | `/ping`, `/api/health`, warm-up de 4 lignes, logs par requête (ref, texte, GPT, SoVITS) |
| 16 | Dépendances | Liste complète : entraînement, WebUI Gradio, tools dataset | Sous-ensemble inférence, roues binaires seulement, pas de compilateur sous Windows |
| 17 | Installation | Manuelle | `setup_tts.py` : révisions et SHA-256 épinglés, reprise, `--verify`, licence affichée, CPU/MPS |
| 18 | Plateformes | Windows/Linux selon l'utilisateur | Windows testé ; Linux/macOS scriptés mais non testés |

**Gains mesurés** (RTX 5080 16 Go, Windows 11, fp16, 3 novembre 2026) :

- 0,34–1,06 s par phrase d'OpenAmika, facteur temps réel 0,13–0,39 ;
- 2,06 Go de VRAM au pic, 1,8 Go alloués après warm-up ;
- démarrage en 20–21 s jusqu'à `/ping ok` ;
- installation complète en 375 s, reprise en 35 s ;
- plus aucune phrase manquante sur les 125 prises de référence, et temps par réponse divisé par deux.

### 6.4 Ce que cela ne change pas

- La qualité intrinsèque de la voix : mêmes poids, même modèle, même 32 kHz.
- Le français : le serveur ne crée pas de frontal français. `main.py` lit le français avec le
  frontal anglais, exactement comme Monika.
- L'entraînement : rien ici ne fine-tune un modèle ; ce sont des correctifs d'inférence.

### 6.5 Et Monika dans tout ça ?

Monika utilise **le GPT-SoVITS de base** (`api_v2.py`, port 9880). Elle compense côté client :

- elle résout elle-même les tags `【happy2】` et choisit le clip de référence et son transcript
  pour chaque run (`voice/gptsovits.py`) ;
- elle envoie une phrase par requête (`TTS_RENDER=sentence`) ou un bloc ;
- elle insère ses propres pauses `naturel` et nettoie le texte (unités, liens, code) ;
- elle rééchantillonne si le serveur répond à un autre taux et coupe la voix après 3 échecs.

Mais elle **n'hérite d'aucun des correctifs 5 à 15** : références longues refusées, phrases
manquantes possibles, pas de garde-fou de drop, pas de trim, pas de cache de prompt, pas de
file d'attente, pas d'Opus. C'est la principale raison pour laquelle sa voix est moins fiable
et moins réactive que celle d'OpenAmika, à moteur égal.

**Meilleure : OpenAmika, sans ambiguïté, pour le serveur.** Monika a la meilleure intégration
côté français et la meilleure gestion des pauses, mais elle s'appuie sur un moteur brut.

**Améliorations**

- Monika : le plus rentable est de reprendre le serveur d'OpenAmika (MIT) et d'écrire un petit
  adaptateur `api_v2` → contrat Socket.IO, ou de porter les correctifs 8 à 12 dans un wrapper.
  Cela donnerait 80 % du gain vocal sans changer de modèle.
- OpenAmika : le français reste le trou béant ; ajouter un frontal ou un second moteur est le
  seul vrai saut de qualité pour un public non anglophone.

---

## 7. Avatar et interface

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Technologie | VRM 3D, three.js + three-vrm | Live2D Cubism, AmikaV4 | Selon l'usage |
| Contenu | Salle du club procédurale, une quinzaine de clips | 142 motions, 25 expressions, 69 clés d'émotion | **OpenAmika** |
| Lip-sync | RMS audio, moteur OpenSakura | Audio par phrase, moteur Live2D | Égalité |
| Helpers | Non | Paper doll `Host`, 64 motions, bulles | **OpenAmika** |
| Personnalisation | Clips ajoutables via `clips.json` | `banks.json`, mapping des émotions | Égalité |
| Dashboard | Chat + 7 blocs : contexte, relation, mémoire, sessions, recettes, outils, réglages | Use, Setup, Tools, About, carte `/flow` | **Monika** pour le contrôle |
| Première expérience | Page directe, `.env` à écrire | Wizard nom/ville/clé, aperçu vocal | **OpenAmika** |
| Mobile | Responsive, Tailscale documenté | Tailscale serve, responsive non documenté | **Monika** |
| Santé | 7 voyants, test à la demande | Voyant global + lignes du launcher | Égalité |

**Meilleure : OpenAmika pour le spectacle et la richesse de l'avatar ; Monika pour le contrôle
et la lisibilité de l'interface.**

**Améliorations**

- Monika : un mode Live2D optionnel (le contrat de modèle est déjà connu via OpenSakura) ;
  plus de clips et des transitions ; un wizard de premier lancement.
- OpenAmika : un panneau unique pour la mémoire, les sessions et les outils, comme le dashboard
  de Monika ; documenter le comportement mobile.

---

## 8. Mémoire et documents

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Mémoire-preuve | Faits + citations littérales, corrections `superseded` | Même principe, avec quarantaine des faits non prouvés | Égalité |
| Rappel | BM25 maison + fastembed, fusion par rang | BM25 + bge-m3 local (768 dims), numpy | Égalité, bge-m3 plus lourd |
| Budget mémoire | 1 800 caractères par bloc | Budgets par section, transcript roulant | Égalité |
| RAG documents | Oui : llama-index + Qdrant local, PDF/DOCX/code, citations chemin+lignes/page | Non | **Monika** |
| Recherche hybride + rerank | Oui, reranker Jina, seuil `RAG_MIN_SCORE` | Non | **Monika** |
| Index incrémental | Oui, manifeste SQLite, reprise | Non applicable | **Monika** |
| Exclusion des secrets | Oui, fichiers et contenu | Non documentée | **Monika** |
| Sessions | SQLite FTS5, reprise, recherche, rétention 30 j | Transcript roulant, reset/backups | **Monika** |
| Sauvegardes/restauration | Archives de notebook, reset de relation | Backups transactionnels, reset complet | **OpenAmika** |
| Gestion visuelle | Panneaux mémoire/journal/sessions | Page Tools, pas de panneau mémoire dédié | **Monika** |

**Meilleure : Monika pour la connaissance personnelle ; OpenAmika pour la mécanique de
sauvegarde/restauration de sa mémoire conversationnelle.**

**Améliorations**

- Monika : OCR des PDF scannés, veille de dossiers, mémoires par projet, détection de
  contradictions entre faits, décroissance temporelle.
- OpenAmika : ingestion de documents avec citations (la brique manque le plus pour un usage
  « assistant »), déduplication des faits, export lisible.

---

## 9. Outils, MCP et helpers

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| MCP | stdio et HTTP, `${VAR}`, `enabled_tools`/`disabled_tools`, outils lents | stdio et HTTP, page Tools, autostart, health probes | Égalité |
| Outils intégrés | Mémoire, documents, `lire_resultat`, planification | Outils généraux, IoT, helpers | **OpenAmika** en breadth |
| Auto-écriture d'outils | `create_custom_tools` : génération, vérification statique, sandbox bwrap/Docker, empreinte | Non | **Monika** |
| Approbation des actions | Aucune : elle agit directement | Clerk post-parole, citation vérifiée, approbations | **OpenAmika** |
| Pont Hermes/nanoMuse | 5 outils `ask/status/answer/stop/health`, générique | Pont first-party + paper doll + polling | **OpenAmika** pour l'intégration |
| Résultats non fiables | Enveloppe `<donnees_outil>`, balises neutralisées | Clerk et citation vérifiée | Égalité |
| Contexte des outils | `/context`, poids par source, schémas à chaque appel | Page Tools, outils désactivables | **Monika** pour le contrôle |
| Sécurité des outils auto-écrits | Sandbox seulement au test ; ensuite accès complet au disque | Pas d'auto-écriture, donc pas ce risque | **OpenAmika** |

**Meilleure : OpenAmika pour un usage domestique encadré ; Monika pour un utilisateur qui veut
que l'IA étende elle-même ses capacités.**

**Améliorations**

- Monika : approbation optionnelle par niveau de risque (lecture libre, écriture confirmée,
  commandes système confirmées) ; sandbox aussi à l'exécution, pas seulement au test ;
  hot-reload des serveurs sans redémarrage.
- OpenAmika : reprise de `create_custom_tools`, mais avec approbation et sandbox permanente ;
  un registre unifié des outils avec poids de contexte.

---

## 10. Autonomie et personnalité

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Rappels/tâches | Oui : langage naturel, récurrences, rattrapage, 50 max | Non | **Monika** |
| Briefing matinal | Oui : météo Open-Meteo, agenda/mail via MCP | Non | **Monika** |
| Recettes apprises | Oui : brouillon → validation, versions, usages | Non | **Monika** |
| Réflexion entre les tours | Oui, optionnelle, peut prendre la parole | Oui : indice, plan, prise de parole | **OpenAmika** pour la structure |
| Scènes quotidiennes | Non | Oui : 5 possibilités par créneau, seedées par date | **OpenAmika** |
| Journal/relation | Oui : favor, journal, revue nocturne Obsidian | Non : mémoire seulement | **Monika** |
| Mots sensibles | Oui : 3 niveaux, liste utilisateur, audit | Aucun filtre équivalent documenté | **Monika** |
| Proactivité | Réflexion, briefing, rappels | Scènes, coach, parole spontanée | **OpenAmika** pour la présence |
| Contrôle utilisateur | Commandes, panneaux, tout supprimable | Page Use, approbations | **Monika** |

**Meilleure : Monika pour l'assistant proactif et planifié ; OpenAmika pour la présence
quotidienne et le jeu de rôle.**

**Améliorations**

- Monika : scènes quotidiennes et humeur de fond, pour éviter la répétition ; un mode
  « présence » plus vivant sans devenir intrusif.
- OpenAmika : planificateur de rappels et briefing, les deux manques les plus visibles pour un
  usage assistant ; recettes apprises.

---

## 11. Vision, messageries, IoT, accès distant

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Vision caméra | Non | Oui : Qwen3-VL-2B local, 16 Go VRAM | **OpenAmika** |
| Discord/Telegram | Non | Oui, service dédié | **OpenAmika** |
| Domotique | Non | Oui : Zigbee/MQTT, serveur MCP, appareils interdits | **OpenAmika** |
| Téléphone | Tailscale, commande `--telephone` | `Tailscale_Serve_OpenAmika.bat`, port 8447 | **OpenAmika** pour le un-clic |
| Authentification distante | `WEB_TOKEN`, cookie HttpOnly | Pas de token, liaison locale/Tailscale | **Monika** |
| Multi-utilisateur | Non, mono-utilisateur | Non, mais messageries peuvent parler à plusieurs | **OpenAmika** |

**Meilleure : OpenAmika, sans conteste, pour tout ce qui sort du navigateur local.**

**Améliorations**

- Monika : caméra optionnelle via un serveur MCP de vision ; passerelle Discord/Telegram
  minimale ; garder l'approbation pour les actions domotiques.
- OpenAmika : token d'accès à la page même en local, détection de port, documentation mobile.

---

## 12. Sécurité, données et vie privée

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Stockage local | `~/.config/monika`, 0700/0600 | `brain/`, `config/`, `memory_f/` | Égalité |
| Chiffrement au repos | Non, permissions seulement | Non, permissions seulement | Égalité |
| Masquage des secrets | Oui, logs/sessions/recettes | Non documenté | **Monika** |
| Journal d'audit | Oui, `audit.jsonl` | Ledger d'usage sans prompts | **Monika** |
| Rétention | 14 j résultats, 30 j sessions | Backups et reset | **Monika** |
| Nuage par défaut | Un seul fournisseur configuré | OpenRouter par défaut | **Monika** |
| Mode 100 % local | Possible, non guidé | Ollama documenté | **OpenAmika** |
| Approbation des actions | Aucune | Oui, clerk + IoT | **OpenAmika** |
| Filtre de mots sensibles | Oui | Non documenté | **Monika** |
| En-têtes web | Pas de CSP ni de contrôle d'`Origin` (documenté) | Non documenté | Égalité, les deux faibles |

**Meilleure : Monika pour la confidentialité des données et la traçabilité ; OpenAmika pour
la sécurité des actions et la domotique.**

**Améliorations**

- Monika : CSP et contrôle d'`Origin`, chiffrement optionnel de `sessions.db` et de
  `notes.json`, approbation des outils destructifs, rotation du token.
- OpenAmika : token de page, masquage des secrets dans les logs, chiffrement des sauvegardes,
  plafond de dépense, et un premier lancement qui propose explicitement le mode local.

---

## 13. Qualité, tests, documentation, distribution

| Fonctionnalité | Monika | OpenAmika | Avantage |
|---|---|---|---|
| Tests | 13 fichiers pytest | 59 fichiers, workflow GitHub Actions, `-k "not live"` | **OpenAmika** |
| CI | Pas de workflow visible | Oui | **OpenAmika** |
| Docs | 10 documents ciblés, en anglais | 3 README (EN/JA/ZH), manuel agent de ~1 000 lignes, 7 docs | **OpenAmika** en volume |
| Docs pour francophone | README et interface FR, docs techniques EN | Rien en français | **Monika** |
| Code | ~petit, lisible, paquets à la racine | Plus gros, contrats, tests de parité | Selon l'usage |
| Distribution | Locale | GitHub, releases, Hugging Face | **OpenAmika** |
| Licence code | Personnelle | MIT | **OpenAmika** |
| Facilité de contribution | Faible (pas de CI, licence floue) | Bonne (MIT, tests, docs) | **OpenAmika** |

**Meilleure : OpenAmika pour la qualité d'ingénierie et la réutilisabilité ; Monika pour
un francophone qui veut lire et modifier un petit code.**

**Améliorations**

- Monika : CI GitHub Actions, `pyproject.toml`, licence explicite, doc de contribution,
  tests de non-régression sur la voix et le RAG.
- OpenAmika : documentation française, guide de contribution, tests de charge sur la latence
  vocale, davantage de tests « live » isolés.

---

## 14. Quel projet pour quel usage ?

| Profil | Recommandation | Pourquoi |
|---|---|---|
| Assistant personnel francophone sur laptop | **Monika** | Léger, CPU, RAG documents, rappels, interface FR |
| Compagne anime sur PC gamer Windows/NVIDIA | **OpenAmika** | Live2D, voix, barge-in, vision, scènes |
| Travail sur des documents et des notes | **Monika** | Seul à avoir un RAG local avec citations |
| Domotique et caméra | **OpenAmika** | IoT Zigbee/MQTT et Qwen3-VL intégrés |
| Confidentialité maximale | **OpenAmika** si Ollama, **Monika** pour la traçabilité | OpenAmika a un mode local guidé ; Monika masque et journalise mieux |
| Budget matériel nul | **Monika** | Aucun GPU requis |
| Hackabilité et ajout d'outils | **Monika** | Auto-écriture d'outils, petit code |
| Réutilisation du code dans un autre projet | **OpenAmika** | MIT, tests, contrats clairs |
| Expérience vocale temps réel | **OpenAmika** | Serveur TTS corrigé, barge-in, latence mesurée |
| Français à l'oral | **Monika**, avec réserves | Seule à le tenter, mais via le frontal anglais |

---

## 15. Plan d'amélioration priorisé

### 15.1 Monika

| Priorité | Amélioration | Impact | Effort |
|---|---|---|---|
| P0 | TTS français natif (Piper/XTTS/F5-TTS) ou fine-tuning GPT-SoVITS FR | Très élevé | Moyen |
| P0 | Barge-in + annulation d'écho, écoute pendant la parole | Très élevé | Moyen |
| P0 | Reprendre les correctifs du serveur OpenAmika (phrases, EOS, dropout, trim, refs longues) | Élevé | Moyen |
| P1 | Presets Ollama/OpenRouter + compteur de coût par tour | Élevé | Faible |
| P1 | CI, `pyproject.toml`, Docker, installateur | Moyen | Faible |
| P1 | CSP, contrôle d'`Origin`, approbation des outils destructifs | Élevé | Faible |
| P2 | Vision caméra via MCP, passerelle Discord/Telegram | Moyen | Moyen |
| P2 | OCR PDF, veille de dossiers, mémoires par projet | Moyen | Moyen |
| P2 | Scènes quotidiennes et humeur de fond | Moyen | Faible |

### 15.2 OpenAmika

| Priorité | Amélioration | Impact | Effort |
|---|---|---|---|
| P0 | RAG documents avec citations | Très élevé | Moyen |
| P0 | Rappels, briefing, recettes apprises | Très élevé | Moyen |
| P0 | Linux/macOS testés ou Docker ; réduction des 5 processus/45 Go | Élevé | Élevé |
| P1 | Token de page, masquage des logs, chiffrement des sauvegardes | Élevé | Faible |
| P1 | Mapping 69→67 complet, clips manquants, fallback documenté | Moyen | Faible |
| P1 | Plafond de dépense par tour/jour | Élevé | Faible |
| P2 | Auto-écriture d'outils avec sandbox permanente et approbation | Élevé | Élevé |
| P2 | Français (interface, prompts, voix) | Élevé pour ce public | Élevé |
| P2 | Détection de port TTS, mise à jour intégrée | Moyen | Faible |

### 15.3 Ce qu'il ne faut pas copier

- **Monika → OpenAmika** : l'absence d'approbation et l'exécution d'outils auto-écrits avec
  les droits de l'utilisateur. Si OpenAmika reprend `create_custom_tools`, il faut une
  approbation et un sandbox permanent.
- **OpenAmika → Monika** : le nuage par défaut, l'absence de token de page et les 5 processus.
  Monika doit rester locale, légère et traçable.
- **Les deux** : l'absence de CSP et de contrôle d'`Origin` sur la page web.

---

## 16. Limites de cette comparaison

- Aucun benchmark n'a été exécuté ici : les chiffres de latence et de VRAM sont ceux
  qu'OpenAmika documente, pas une mesure indépendante.
- Les verdicts sont contextuels. « Meilleure » ne veut pas dire « supérieure partout » :
  Monika et OpenAmika ne visent pas le même utilisateur.
- Le code évolue vite (le dépôt a quelques semaines) ; ce document décrit l'état du
  9 novembre 2026.
- Certaines fonctionnalités d'OpenAmika n'ont pas d'équivalent documenté (filtre de mots
  sensibles, contrôle d'`Origin`) : l'absence dans ce document signifie « non trouvé dans
  le code ou la docs », pas nécessairement « absent du binaire ».
- La comparaison porte sur les deux IA et leurs serveurs vocaux ; elle ne juge pas la qualité
  artistique des personnages ni le contenu des scénarios.
