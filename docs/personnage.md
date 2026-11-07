# Personnage : émotions, relation et journal

Monika a un état intérieur qui colore son ton et se voit sur l'avatar. Il se règle avec
`CHARACTER_ENABLED` (désactivé : elle se comporte exactement comme sans personnage). Le code est
dans `character/` : bibliothèque standard uniquement, indépendante de la boucle d'agent, qui ne
connaît qu'une porte, `CharacterService` (`character/service.py`).

**Principes à garder**

- **Le LLM ne décide jamais d'un état.** Il propose une valence et des signaux ; le code les borne
  et calcule les émotions. Un message (ou une note) ne peut pas faire bondir la favorabilité.
- **Rien n'est retenu sans tour complet.** Émotions et favorabilité ne changent qu'à la fin d'une
  réponse complète ; un tour interrompu ne compte pas.
- **Le texte venu d'un modèle est non fiable** : nettoyé, borné, sans balises, injecté comme des
  « pensées » et jamais comme des ordres. Les résultats d'outils n'entrent jamais ici.
- **Jamais d'hostilité** : un niveau bas rend Monika plus réservée, pas désagréable. Son caractère
  (« jamais de culpabilisation, de jalousie ni de manipulation ») ne change pas.
- **Aucune méthode de cette couche ne doit faire échouer un tour** : chaque échec est journalisé et
  la conversation continue sans elle.
- **Tout est visible et supprimable** par toi (`/relation`, `/journal`, tableau de bord).

## Le cycle d'un tour

```
message  ──►  start_turn ──► begin()      lit le ton en arrière-plan (LLM court, repli lexique)
                              │             pendant que la mémoire est rappelée
                              ▼
                        ready()           réaction d'avatar  +  bloc de prompt (relation, humeur)
                                           (+ bloc journal, lu au début du tour)
réponse en flux ──► feed(texte)           gestes d'avatar qui suivent le ton de sa réponse
tour complet ──► commit(réponse)          émotions et favorabilité changent et sont sauvegardées ;
                                           l'échange est proposé au journal
tour interrompu ──► close()               rien n'est retenu, la lecture du ton est annulée
fermeture ──► aclose()                    dernière note du journal, puis attente de l'écriture
```

## Émotions

26 émotions : six de base (`joyeuse`, `tristesse`, `colere`, `peur`, `surprise`, `degout`) et vingt
nuances (`taquine`, `boudeuse`, `jalousie`, `timide`, `fiere`, `attentionnee`, `tsundere`…). Leur liste,
leurs demi-vies, leurs plafonds et leurs tags de voix vivent dans **une seule table**,
`character/emotion_spec.py`. Les poids d'expressions du visage sont ceux d'OpenSakura
(`EMOTION_EXPR` dans `web/static/js/avatar/animation.js`), où chaque émotion française est reliée à sa
clé anglaise. « Neutre » est dérivée (ce que l'émotion la plus forte
laisse libre), jamais stockée. Au plus trois émotions comptent à la fois : les plus faibles retombent
au seuil, et la page ne dessine que celles qui vivent.

- **Lecture du ton** : chaque message est lu par un petit appel au modèle (`AFFECT_LLM_MODEL`,
  vide = `MODEL_NAME` ; délai `AFFECT_TIMEOUT_S`, 2 s par défaut), en parallèle du rappel de la
  mémoire. S'il échoue ou tarde, un lexique français intégré (mots, emojis, négations, majuscules)
  prend le relais. La réponse du modèle est analysée strictement : seuls les champs autorisés sont
  gardés et tous les nombres sont bornés. Une tristesse ou un problème technique n'est jamais pris
  pour de l'hostilité.
- **Évolution** : un message ne déplace pas une émotion de plus de 0,5. Elles s'estompent seules
  avec le temps, y compris d'une session à l'autre (demi-vie : 25 s pour la surprise, 10 min pour la
  taquinerie, 15 min pour la joie, 20 min pour la bouderie). Joie et bouderie s'inhibent.
- **Plafonds selon la relation** : taquiner et bouder demandent de la familiarité (un niveau bas
  plafonne ces émotions).

## Relation (favorabilité)

Un score lent et persistant, avec quatre niveaux : réservée, cordiale, complice, proche.

- Il part de 0,30 et progresse avec la régularité (jours de présence, séries) et le ton des
  échanges, mais il est plafonné par jour (+0,06 au plus, −0,05 au plus) : un seul message ne peut
  pas le faire bondir. L'impolitesse pèse un peu plus que la gentillesse.
- Seuils de favorabilité : 0,25 (cordiale), 0,50 (complice), 0,75 (proche). Complice demande aussi
  30 messages et 4 jours différents, proche 100 messages et 14 jours : la proximité prend du temps.
- Une longue absence ne fait jamais redescendre de niveau (légère dérive après 3 jours, plancher
  au seuil du niveau actuel). Une marge d'hystérésis évite les va-et-vient entre deux niveaux.
- À bas niveau Monika est plus réservée, jamais désagréable.
- **Transparence** : `/relation` affiche l'état, `/relation reinitialiser` le remet à zéro.

## Dans ses réponses et sur l'avatar

- **Prompt** : un court bloc à la fin du prompt indique le niveau de familiarité et l'humeur du
  moment. Il ne contient que du texte écrit dans le code, jamais du texte venant de toi.
- **Avatar** : une réaction à ton message (salut de la main, hochement, rougissement, surprise,
  moue…), puis des gestes qui suivent le ton de sa propre réponse (idée, rire, joie), avec
  retenue : 8 s de pause entre deux gestes, trois au plus par réponse, jamais deux fois le même.

## Le journal de Monika

En plus de son carnet de mémoire (les notes qu'elle réécrit sur toi), Monika tient un **journal** de ses
impressions sur toi, écrit de son point de vue : comment tu sembles aller, ce qui compte pour toi,
ce qui vous rapproche, ce qu'elle voudrait reprendre. Cela donne une continuité plus humaine d'une
conversation à l'autre.

- **Quand** : une note tous les `JOURNAL_EVERY_TURNS` échanges (8 par défaut) et à la fermeture,
  en arrière-plan : tu n'attends jamais. Les échanges trop pauvres (« ok », « merci ») ne donnent
  pas de note. Chaque écriture met aussi à jour son *impression d'ensemble* et les *sujets à
  reprendre*, ce qui évite que le journal gonfle indéfiniment (40 notes au plus).
- **Dans ses réponses** : l'impression, les sujets à reprendre et les trois dernières notes sont
  ajoutés à son prompt (taille plafonnée par `JOURNAL_MAX_CHARS`), présentés comme des pensées
  qui peuvent être fausses ou périmées, jamais comme des ordres : ce que tu dis maintenant prime.
- **Ce qui est écrit** : seulement tes messages et ses réponses, jamais les résultats d'outils ;
  les secrets sont masqués avant l'envoi et dans ce qui revient. Le texte du journal est nettoyé
  (pas de balises, une seule ligne par note) : il ne peut pas se faire passer pour une consigne.
- **Tu gardes la main** : `/journal` affiche tout, `/journal oublier ID` supprime une note,
  `/journal effacer` repart d'une page blanche, et `JOURNAL_ENABLED=false` désactive la fonction.
  Les extraits d'échanges servant à écrire une note sont envoyés au fournisseur LLM (modèle
  `JOURNAL_LLM_MODEL`, vide = `MODEL_NAME`), comme le reste de la conversation.

## Les modules de `character/`

| Fichier | Rôle |
|---|---|
| `emotion_spec.py` | La table des 26 émotions : la seule déclaration d'une émotion (demi-vie, plafonds par niveau, tags de voix, gestes de repli) |
| `models.py` | Types communs : `Appraisal`, `AvatarCue`, listes dérivées des émotions et signaux autorisés |
| `appraisal.py` | Lecture du ton : lexique français, LLM (sortie non fiable, validée strictement), repli. **Seul endroit** qui transforme un ton en variations d'émotions (`deltas_from`) |
| `emotions.py` | Vecteur des 26 émotions, décroissance dans le temps (aussi entre sessions), inhibition joie/bouderie, au plus 3 émotions notables |
| `relationship.py` | Favorabilité : paliers, plafonds par jour, maturité requise (jours et messages), hystérésis |
| `core.py` | Émotions + relation validées ensemble ; `preview()` calcule l'état d'un tour **sans rien changer** |
| `tone.py` | Texte du bloc de prompt (niveau, humeur). Ne contient jamais de texte venant de l'utilisateur |
| `cues.py` | Quel geste d'avatar : réaction au message, gestes dans la réponse (avec retenue) |
| `journal.py` | Stockage, nettoyage et rendu du journal (impression, sujets, notes) |
| `journal_keeper.py` | Écriture des notes en arrière-plan (file séquentielle, masquage, JSON strict) |
| `store.py` | JSON atomique en `0600`, fichier abîmé mis de côté (`.corrupt-…`) |
| `service.py` | La façade (`CharacterService` / `CharacterTurn`) et l'assemblage (`create_service`) |

Côté page : `web/static/js/avatar/` (clips, retargeting, visage, gestes), `web/static/js/panels.js`
(tableau de bord), `web/dashboard.py` (actions), `web/chat.py` (conversation) et `web/server.py`
(routes).

## Modifier le comportement

- **Ajouter un clip** : copier le `.glb` dans `web/static/assets/clips/`, l'ajouter à
  `web/static/assets/clips.json`, puis l'utiliser dans `cues.py`. `tests/test_clips.py` refuse un nom
  utilisé par le code mais absent du manifeste, et un fichier que rien ne joue. En `once: true` le
  clip se joue une fois et tient sa dernière pose ; `fade` règle le fondu d'entrée (0 = coupure). La page ignore les noms de clips
  qu'elle ne connaît pas.
- **Ajouter un geste dans ses réponses** : une ligne dans `_REPLY_RULES` (`cues.py`).
- **Ajouter une réaction à un message** : `_REACTIONS` (`cues.py`) ; un nouveau *signal* se déclare
  d'abord dans `SIGNALS` (`models.py`) et se détecte dans `appraisal.py`.
- **Régler la progression** : constantes en tête de `relationship.py` (rythme, plafonds, paliers).
- **Régler le ton écrit** : `LEVEL_GUIDANCE` et `MOOD_GUIDANCE` dans `tone.py`.
- **Régler le journal** : `SYSTEM_PROMPT` dans `journal_keeper.py`, tailles dans `journal.py`.
- **Ajouter une émotion** : une ligne dans `SPECS` (`character/emotion_spec.py`). Le reste en découle :
  listes et signaux (`models.py`), demi-vies et inhibition (`emotions.py`), plafonds (`relationship.py`),
  `GET /api/emotions` et les libellés du tableau de bord. Le visage demande en plus une ligne dans
  `FRENCH_EMOTIONS` (`web/static/js/avatar/animation.js`) pour lui donner sa clé OpenSakura ; sans elle,
  l'émotion reste neutre de visage.
