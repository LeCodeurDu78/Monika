# Voix : écoute et parole

## Installation

```bash
pip install -r requirements.txt   # aucun modèle de voix ici (voir « La voix GPT-SoVITS »)
```

`parec` et `pacat` viennent du paquet `libpulse` (PulseAudio ou PipeWire) : c'est le micro et la
sortie audio. Monika ne charge aucun modèle de voix elle-même : elle parle à **GPT-SoVITS**, lancé
à part. S'il manque `pacat`, Monika le dit au démarrage et continue sans voix.

## Écoute (`--mode voix`)

- **Micro** : `parec` lit la source par défaut de PipeWire/PulseAudio, déjà en 16 kHz mono. Une
  autre source : `pactl list short sources`, puis `AUDIO_INPUT_DEVICE=nom_de_la_source`. Un micro
  absent ou refusé est signalé tout de suite, avant même de toucher à la voix.
- **Fin de phrase (VAD)** : le modèle Silero embarqué dans faster-whisper analyse des images de
  32 ms ; la parole démarre après quelques images confiantes (les claquements sont ignorés) et
  la phrase se termine après `VAD_END_SILENCE_MS` (700 ms) de silence, avec un peu d'audio gardé
  avant et après pour ne pas couper les mots.
- **Transcription** : faster-whisper sur le **CPU** (modèle `small`, int8, français), pour laisser
  la VRAM à la synthèse. Un filtre écarte les hallucinations connues de Whisper sur le bruit
  (« Sous-titres réalisés par… », répétitions) ; les phrases écartées ne déclenchent rien.
- **Demi-duplex** : Monika n'écoute pas pendant qu'elle génère sa réponse ni pendant qu'elle
  parle (plus `VOICE_RESUME_DELAY_MS`, 400 ms) : elle ne s'entend pas elle-même. Pas
  d'interruption à la voix, et **pas de mot d'activation** : elle écoute en continu. Avec des
  haut-parleurs, un casque reste le plus sûr.
- **Quitter** : dis une phrase d'au revoir *entière* (« au revoir Monika », « quitte », « à plus
  tard »), ou Ctrl+C. Une phrase qui contient « quitte » au milieu ne ferme rien.

## Parole

La voix est **GPT-SoVITS**, qui tourne dans son propre programme : Monika lui envoie du texte par
son API HTTP (`api_v2.py`), reçoit un WAV et le joue par `pacat`. Monika parle **phrase par phrase
pendant que le modèle écrit encore**.

- **Modes** : Monika parle en mode voix et en mode texte (`TTS_TEXT_MODE=false` la rend muette
  en mode texte). L'écran garde le texte tel quel ; la voix reçoit une version nettoyée : plus de
  Markdown, de listes, de titres ni d'emojis ; les liens gardent leur texte, les URL deviennent
  « un lien », les chemins gardent leur nom de fichier ; `21°C`, `20 %`, `14:30` se disent « 21
  degrés », « 20 pour cent », « 14 h 30 » ; `2 Go`, `512 Mo`, `3,4 GHz`, `25 Mb/s` se disent « 2
  gigaoctets », « 512 mégaoctets », « 3,4 gigahertz », « 25 mégabits par seconde » ; un bloc de code
  n'est jamais lu (Monika dit une fois qu'elle le laisse à l'écran).
- **Abréviations** : seuls les symboles **collés à un nombre** sont prononcés en toutes lettres
  (capacités, débits, fréquences : `Ko Mo Go To Po Eo`, `KiB MiB GiB TiB`, `KB MB GB TB`, les bits
  `Kb Mb Gb Tb`, `Hz kHz MHz GHz THz`). « le Go », « 3 ans de Go » ou « Go 1.22 » restent tels quels,
  et le pluriel suit la règle française : « 1 gigaoctet », « 0,5 gigaoctet », « 2 gigaoctets ».
  Seule la voix est concernée : l'écran affiche toujours le texte d'origine.
- **Coupures** : Ctrl+C pendant qu'elle parle coupe la voix, la session continue ; en mode
  texte, envoyer un nouveau message coupe aussi ce qui se disait.
- **Panne de voix** (serveur arrêté, référence illisible, sortie audio perdue) : le message est
  affiché en français, les réponses restent à l'écran ; après trois échecs de suite Monika cesse
  de parler jusqu'au prochain lancement.
- **Sortie audio** : `AUDIO_OUTPUT_DEVICE` (liste : `pactl list short sinks`), vide = défaut.

## La voix GPT-SoVITS

1. **Lance le serveur** (dans ta copie de GPT-SoVITS, avec ses propres modèles) :

   ```bash
   python api_v2.py -a 127.0.0.1 -p 9880
   ```

   Plus simple : `./lancer-monika.sh` fait tout — il démarre `api_v2.py` en arrière-plan s'il ne
   tourne pas déjà, attend qu'il écoute, passe une vraie phrase de synthèse avec ton pack de voix
   pour vérifier (nom de la voix, fréquence, tags, durée), puis ouvre Monika ; en sortant, il arrête
   le serveur qu'il a démarré. `GPT_SOVITS_DIR` dit où il se trouve (défaut
   `~/Applications/GPT-SoVITS`), `GPT_SOVITS_LOG` où écrire son journal.

   Monika s'y connecte au démarrage (`TTS_URL`, `http://127.0.0.1:9880` par défaut) et lui envoie
   une phrase courte pour préchauffer le modèle : le premier appel peut prendre une dizaine de
   secondes, les suivants beaucoup moins. `api_v2.py` **n'a pas d'authentification** : garde-le sur
   `127.0.0.1`, ne l'expose jamais sur le réseau.
2. **Dépose un pack de voix** dans `~/.config/monika/voice/` (`TTS_SPEAKERS_DIR` pour un autre
   dossier), avec la structure de GPT-SoVITS :

   ```
   ~/.config/monika/voice/ma-voix/
     character.json               optionnel : { "character_id": "ma-voix", "name": "…", "language": "Chinese" }
     references/emotions.json     { "neutral1": { "audio": "neutral1.wav", "text": "transcript de référence" }, … }
     references/neutral1.wav      un enregistrement par tag (happy1.wav, sad2.wav, angry3.wav…)
   ```

   Copie le dossier `references/` d'un pack d'un moteur GPT-SoVITS (les poids `.ckpt`/`.pth` ne
   servent pas : seul le serveur les charge). Plusieurs dossiers dans la racine = il faut choisir
   avec `TTS_CHARACTER_ID` (le nom du dossier ou le `character_id`). Les chemins envoyés au serveur
   sont **locaux** : les deux programmes doivent tourner sur la même machine.
3. **La langue** : GPT-SoVITS n'a pas de frontal français. `TTS_LANGUAGE=en` (défaut) fait lire le
   texte latin par le frontal anglais ; `auto`, `zh`, `ja`, `ko` et `yue` sont aussi acceptés, et
   `fr` est traité comme `en`. La voix de référence doit exister dans la langue choisie :
   `TTS_PROMPT_LANG` (défaut `en`) décrit les transcripts du pack, et un avertissement s'affiche
   quand le pack déclare une autre langue. Sans `character.json`, le pack doit donc être anglais
   pour `LANGUAGE=en`, français pour `LANGUAGE=fr`.

- **Tags d'émotion** : l'émotion de Monika devient `【happy2】`-like dans le texte, avec au plus
  deux tags par phrase (une transition commence neutre et arrive à l'émotion au milieu). Chaque run
  de texte est synthétisé avec **sa** référence : un changement de tag au milieu d'un bloc ne coûte
  qu'une requête de plus. `TTS_EMOTION_TAGS=auto` n'utilise **que** les tags qui ont un fichier
  (`neutral1` sinon) : la voix ne prétend jamais ressentir une émotion dont elle n'a pas
  l'enregistrement. `on` force tous les tags de la table, `off` parle toujours neutre.
- **Fréquence** : celle annoncée par le WAV du préchauffage (32000 Hz chez GPT-SoVITS). Si le
  serveur répond ensuite à une autre fréquence, Monika rééchantillonne : le son ne sort ni trop
  vite ni trop lent.
- **Latence** : `TTS_RENDER=sentence` (défaut) envoie une phrase par requête, donc le premier son
  arrive vite. `TTS_RENDER=block` regroupe les phrases jusqu'à `TTS_BLOCK_MAX_CHARS` : meilleure
  prosodie sur les longues réponses, premier son plus tardif. `TTS_TIMEOUT_S` borne l'attente.
- **Pauses** : `TTS_PAUSE_STYLE=neutral` (`neutre` reste accepté ; défaut) ne change rien, les phrases s'enchaînent comme
  avant. `naturel` insère un court silence **après** chaque phrase, selon sa ponctuation
  (« . » 0,50 s, « ? » 0,45, « ! » 0,35, « … » 0,75, une virgule finale 0,25) et son émotion :
  une voix triste ou déçue traîne (×1,35), en colère ou excitée se presse (×0,65 à 0,75), et
  l'intensité du sentiment module l'écart. `TTS_PAUSE_SCALE` multiplie le tout, `TTS_PAUSE_MAX_S`
  plafonne une pause (1,2 s par défaut). Ce sont **des silences seulement**, jamais de bruit
  d'hésitation : aucun « euh » n'est ajouté. Rien n'est inséré avant le premier mot d'une réponse,
  ni après son dernier — la parole commence et se déroule sans l'attendre. Réglable à chaud
  depuis `/settings` (groupe *Voix*). La règle vit dans `voice/pauses.py`.
- **Panne** : serveur arrêté ou lent → message clair, réponses à l'écran, et la conversation
  continue ; trois échecs de suite coupent la voix.

## Parler à Monika dans le navigateur

```text
[ 🎙 Parler ]  →  micro du navigateur  →  AudioWorklet 16 kHz  →  VAD + Whisper (les mêmes)
                                                            ↓
           haut-parleurs de l'appareil  ←  WebAudio  ←  synthèse GPT-SoVITS (la même voix)
```

- **Même pipeline que le mode voix** : la page n'ajoute ni second détecteur de parole ni second
  Whisper. Un AudioWorklet (dans `web/static/js/voice.js`) ramène le micro à 16 kHz mono et
  l'envoie en trames de 512 échantillons par le WebSocket ; elles entrent dans le même
  `VoiceListener` que `--mode voix` (où le micro vient de `parec`). `parec`/`pacat` ne sont pas
  touchés : la conversation à voix haute en local continue de marcher.
- **La réponse se fait entendre** : quand le micro de la page est ouvert, le `Speaker` écrit dans
  la page (`WebAudio`, `web/voice.py`) au lieu de pacat ; en refermant le micro, la voix revient à
  la sortie de la machine. Les pauses entre phrases (voir *Pauses* plus haut) s'entendent
  évidemment aussi ici, puisque c'est le même flux PCM.
- **Une seule voix à la fois** : un second appareil qui ouvre le micro reprend le flux audio ; le
  premier n'entend plus rien tant qu'il ne l'a pas repris. Fermer la page ferme le micro (le
  WebSocket qui tombe coupe l'écoute côté serveur).
- **Permissions, proprement** : un refus, l'absence de micro, un micro déjà pris par un autre
  programme ou un navigateur sans `getUserMedia` donnent chacun une phrase claire sous la
  conversation, et **le bouton reste au repos** : rien n'est envoyé au serveur, aucune session ne
  s'ouvre.
- **HTTPS obligatoire (ou localhost)** : les navigateurs ne donnent le micro qu'à une origine
  sécurisée. `http://localhost:8765` marche ; une page ouverte depuis un autre appareil en
  `http://` **non** — d'où `tailscale serve` (HTTPS) pour le téléphone. Le message le dit au lieu
  d'échouer en silence.
- **Half duplex** : le micro se rouvre quand la réponse a fini d'être **entendue**, pas quand elle a
  fini d'être envoyée (le serveur tient l'horloge de lecture) ; l'annulation d'écho du navigateur
  fait le reste sur un haut-parleur ouvert.
- **Limite** : pas de mot d'éveil, pas de barge-in (parler par-dessus Monika ne la coupe pas), et
  la voix dans la page demande une synthèse qui répond — sans GPT-SoVITS, le micro marche toujours
  mais les réponses restent à l'écran.
