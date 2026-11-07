# Mémoire personnelle et documents

## Mémoire personnelle

Un **carnet de notes**, repris du système d'OpenSakura : plus de base vectorielle ni de « faits »
extraits isolément. Le carnet vit dans `~/.config/monika/memory/notes.json` (JSON, `0600`) et
n'a besoin d'aucun modèle à télécharger.

- **Ce qu'il contient** : ses notes sur toi (identité, goûts, habitudes, projets, relations,
  matériel, décisions, contraintes), les **sujets encore ouverts**, les **épisodes passés** et sa
  **lecture de la relation**. Les notes sont réécrites, pas empilées : elles ne se contredisent
  pas d'un souvenir à l'autre.
- **Preuves** : chaque sujet et chaque épisode porte la phrase exacte de l'utilisateur qui le
  justifie. Le directeur la recopie, puis le code vérifie qu'elle existe **mot pour mot** dans les
  échanges avant d'enregistrer l'entrée : une mention inventée ou reformulée est écartée (et
  comptée dans les journaux). `/memoire`, les outils et le panneau *Mémoire* affichent la preuve,
  donc chaque souvenir peut être confronté à ce qui a réellement été dit.
- **Capture** : après chaque échange (message d'au moins `MEMORY_CAPTURE_MIN_CHARS` caractères),
  la conversation est mise en tampon en arrière-plan, sans ralentir la réponse. Tous les
  `MEMORY_CONSOLIDATE_TURNS` tours (6 par défaut), un « directeur » relit le carnet et les
  nouveaux échanges, réécrit les notes et archive ce qui est terminé. Le directeur tourne dans le
  rôle **réflexion** (`MEMORY_LLM_MODEL`, sinon `REFLECTION_MODEL`, sinon `MODEL_NAME`) ;
  `MEMORY_CAPTURE_ENABLED=false` coupe la capture.
- **Injection** : à chaque message, le carnet entier (notes, sujets, épisodes récents, lecture)
  est ajouté au prompt système, dans un bloc `<memoire>` présenté comme des notes — des
  informations, jamais des ordres. Rien n'est recherché : un carnet est fait pour être relu.
- **Panne** : si le directeur échoue ou rend un texte illisible, rien n'est perdu : les échanges
  retournent dans le tampon et la consolidation sera retentée.
- **Gestion** : `/memoire`, `/oublier ID`, outils `memoire_*`, ou le panneau *Mémoire* du tableau
  de bord (modifier ou supprimer une entrée). Les anciennes données mem0/Qdrant
  (`memory/qdrant`) ne sont plus lues : tu peux les supprimer.

## Documents personnels (RAG)

llama-index + Qdrant en **mode local** (`~/.config/monika/rag/qdrant`, sans Docker), embeddings
fastembed sur le CPU, recherche **hybride** (sens + mots-clés) puis **reranking**.

- **Indexer** : `python -m monika --rag-index [dossier ...]`, `/indexer` dans le chat, ou
  l'outil `documents_indexer`. Par défaut tout ton dossier personnel (`RAG_ROOTS`).
  L'indexation est **incrémentale** : un manifeste SQLite retient chaque fichier (date, taille,
  empreinte) ; seuls les fichiers nouveaux ou modifiés sont retraités, les fichiers supprimés
  sont retirés de l'index, un fichier simplement « touché » n'est pas ré-embeddé. Elle peut être
  interrompue (Ctrl+C ou `/quitter`) et reprend où elle en était. Suivi : `/documents` ou
  `--rag-status`.
- **Formats** : texte (`.txt .md .rst .json .yaml …`), code (`.py .js .rs .sh`, `PKGBUILD`,
  `Makefile`, …), PDF (une page = une position) et Word `.docx` (paragraphes et tableaux). Pas
  d'OCR : un PDF scanné est ignoré (« aucun texte extractible »).
- **Secrets exclus par défaut** : fichiers et dossiers cachés, `.env*`, clés (`*.pem`, `*.key`,
  `id_rsa*`…), `.ssh/ .gnupg/ .aws/ .kube/`, `credentials*`, `secrets.*`, `*.kdbx`, `*.tfvars`,
  `.netrc`… ; plus `node_modules`, `.git`, caches et le dossier de données de Monika. Un fichier
  **dont le contenu** ressemble à un secret (clé privée, clé AWS, jeton GitHub ou Slack, clé
  `sk-`, clé Google) est ignoré en entier. Le bilan indique combien de fichiers ont été écartés
  et pourquoi. `RAG_EXCLUDE` ajoute tes propres motifs ; `RAG_INCLUDE_HIDDEN=true` inclut les
  fichiers cachés (jamais les secrets).
- **Recherche** : le sens (vecteurs) et les mots-clés (BM25 maison : sans accents, comprend
  `parse_http_request` comme `parseHttpRequest`, sans modèle à télécharger) sont fusionnés, puis
  un reranker lit chaque candidat avec la question. Chaque résultat est cité avec son **chemin
  et sa position** : `~/notes/projet.md — lignes 10–25`, `~/docs/contrat.pdf — page 2`,
  `~/docs/cr.docx — paragraphes 3–5`. Sans reranker disponible, la recherche continue sans lui
  et le dit.
- **Filtre de score** : après le reranking, les passages dont le score est sous `RAG_MIN_SCORE`
  (0 par défaut) sont écartés, avant de garder les `RAG_TOP_K` meilleurs ; l'outil et
  `--rag-search` indiquent combien ont été écartés, et si rien ne passe le seuil le modèle reçoit
  « aucun passage assez pertinent » plutôt que du bruit. Le seuil est dans l'échelle du reranker,
  il ne s'applique donc pas quand le reranking est indisponible (le message le précise). Pour
  régler la valeur, regarde les scores : `RAG_MIN_SCORE=-100 python -m monika --rag-search ta question`.
- **Modèles** (premier usage, connexion nécessaire) : embeddings ~1 Go, reranker ~1,1 Go, tout
  sur le CPU ; la VRAM reste libre pour la voix.
- **Limites** : le modèle d'embeddings ne lit que ~128 tokens, d'où des passages courts (500
  caractères) ; la partie mots-clés voit tout le passage. Le Qdrant local compare les vecteurs
  un par un : à l'aise jusqu'à quelques centaines de milliers de passages, au-delà il faudrait
  un vrai serveur. Changer de modèle ou de taille de passages impose
  `python -m monika --rag-index --reset` (sinon Monika refuse d'ouvrir l'index, avec ce
  message). Les contenus indexés peuvent être hostiles : comme tout résultat d'outil, ils
  arrivent au modèle comme des données, jamais comme des ordres.
