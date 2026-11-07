# Monika

Assistante IA personnelle, locale et en français.
Inspirée du personnage de Monika (DDLC). Usage strictement personnel.

## Aperçu

Monika discute depuis une page dans le navigateur ou à la voix (avec **ta** voix clonée), agit grâce à des outils
MCP, se souvient de ce qui compte pour toi, retrouve tes documents
et s'affiche sous forme d'avatar 3D avec un petit tableau de bord dans le navigateur.
Tout est stocké chez toi ; seul le fournisseur LLM que tu configures reçoit tes messages.

- **Conversation** dans la page, avec des commandes (`/help`, `/sessions`, `/remind`…) et un bouton
  🎙 pour parler au micro du navigateur.
- **Voix** : écoute par Whisper et parole par GPT-SoVITS, lancé à part.
- **Outils** : serveurs MCP déclarés dans un fichier, plus des outils internes (mémoire, documents)
  et des outils que Monika peut s'écrire elle-même.
- **Mémoire** : un carnet de notes réécrit après chaque échange, plus un index de tes documents
  (RAG) avec citations.
- **Autonome** : rappels et tâches récurrentes, briefing du matin, recettes apprises.
- **Personnage** : émotions, relation et journal, visibles sur l'avatar et dans le tableau de bord.

## Documentation

| Sujet | Document |
|---|---|
| Voix : écoute, parole, GPT-SoVITS, micro de la page | [docs/voix.md](docs/voix.md) |
| Mémoire personnelle et documents (RAG) | [docs/memoire.md](docs/memoire.md) |
| Outils MCP, outils internes et outils perso | [docs/outils.md](docs/outils.md) |
| Agent, contexte, recettes, sessions, tâches, briefing | [docs/agent.md](docs/agent.md) |
| Configuration et structure du projet | [docs/configuration.md](docs/configuration.md) |
| Commandes du terminal et du chat | [docs/commandes.md](docs/commandes.md) |
| Avatar, tableau de bord, réglages, téléphone | [docs/page.md](docs/page.md) |
| Personnage : émotions, relation, journal | [docs/personnage.md](docs/personnage.md) |
| Données, vie privée, sécurité, mots sensibles | [docs/securite.md](docs/securite.md) |
| Dépannage | [docs/depannage.md](docs/depannage.md) |

Pont MCP vers un helper (Hermes, nanoMuse) : [bridges/README.md](bridges/README.md).

## Installation

Lance toujours Monika **depuis le dossier du projet** (`.env` y est cherché ; le projet n'est pas
prévu pour être installé comme paquet).

- **Python 3.12+**.
- Un accès à une **API compatible OpenAI** (clé et URL dans `.env`).
- Pour la voix : `ffmpeg`, PyTorch, et idéalement un GPU (4 Go suffisent ; sinon la synthèse
  tourne sur le CPU, plus lentement) ; le son passe par `parec` et `pacat` (paquet `libpulse`).
- Pour les serveurs MCP de l'exemple : Node.js (`npx`).
- Espace disque pour les modèles téléchargés au premier usage : environ 2 Go (documents),
  2 Go (voix) ; une connexion est nécessaire à ce moment-là. La mémoire, elle, n'a aucun modèle
  à télécharger.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # puis édite LLM_BASE_URL, OPENAI_API_KEY, MODEL_NAME
python -m monika --config     # vérifie la configuration (secrets masqués)
python -m monika               # démarre Monika : la conversation se passe dans la page
```

Le mode voix demande une installation supplémentaire, décrite dans [docs/voix.md](docs/voix.md).

## Lancement

```bash
python -m monika               # la conversation se passe dans la page (http://localhost:8765)
./lancer-monika.sh             # démarre la voix GPT-SoVITS si besoin, puis lance Monika
python -m monika --mode voix   # conversation à voix haute (réponses aussi à l'écran)
python -m monika --config      # affiche la configuration chargée (secrets masqués)
```

Toutes les autres options, les commandes du chat et les réglages : [docs/commandes.md](docs/commandes.md).
