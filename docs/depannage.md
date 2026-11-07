# Dépannage

| Symptôme | Piste |
|---|---|
| `Le port 8765 est déjà utilisé` | Une autre instance tourne : ferme-la ou change `WEB_PORT` |
| Micro non trouvé | `pactl info` doit répondre ; `pactl list short sources` puis `AUDIO_INPUT_DEVICE` |
| Pas de voix | Le message du lancement ; `pacat --version` ; le pack de voix dans `~/.config/monika/voice` |
| `Le serveur GPT-SoVITS (…) ne répond pas` | Lance `api_v2.py` sur `127.0.0.1:9880`, ou corrige `TTS_URL` |
| Voix neutre | Le pack n'a pas d'enregistrement par tag : `TTS_EMOTION_TAGS=auto` n'utilise que ceux qui ont un fichier |
| `Aucun pack de voix GPT-SoVITS dans …` | Il manque `references/emotions.json` (voir [La voix GPT-SoVITS](voix.md#la-voix-gpt-sovits)) |
| Voix qui hache | `TTS_RENDER=block` pour des requêtes plus longues, ou ferme les programmes qui saturent le GPU du serveur |
| Un serveur MCP n'apparaît pas | `python -m monika --tools` ; variable `${…}` absente ; voir `logs/mcp-<nom>.log` |
| `create_custom_tools` refuse de tester | `bwrap --version` ou `docker --version` doit répondre (bubblewrap ou Docker) : sans bac à sable, aucun outil perso n'est créé |
| Un outil perso a disparu après redémarrage | Son code a changé depuis sa vérification : demande-lui de le régénérer (revérifié et retesté) |
| Index qui refuse de s'ouvrir | Modèle ou taille de passages changés : `python -m monika --rag-index --reset` |
| Le carnet ne se met plus à jour | `MEMORY_CAPTURE_ENABLED` ou échec du directeur : voir les journaux ; le carnet reste lisible et modifiable à la main |
| `/sessions` vide ou « sessions désactivées » | `SESSIONS_ENABLED=false` par défaut : l'activer dans `/settings` puis redémarrer |
| `Une autre instance Monika utilise déjà ce dossier` | Un Monika tourne déjà : ouvre sa page au lieu d'en démarrer un second |
| Un rappel ne se déclenche pas | `TASKS_ENABLED=false`, ou aucun processus Monika ne tourne : laisse `python -m monika` ouvert ; `/taches` dit ce qui est prévu |
| Un rappel arrive en retard | Il est tombé pendant un arrêt : la prochaine fois, laisse `python -m monika` tourner |
| Avatar absent | WebGL est requis dans le navigateur ; le tableau de bord marche sans |
