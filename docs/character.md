# Character: emotions, relationship and journal

Monika has an inner state that colours her tone and shows on the avatar. It is controlled by
`CHARACTER_ENABLED` (disabled: she behaves exactly as without a character). The code is in
`character/`: standard library only, independent of the agent loop, which knows only one door,
`CharacterService` (`character/service.py`).

**Principles to keep**

- **The LLM never decides a state.** It proposes a valence and signals; the code bounds them
  and computes the emotions. A message (or a note) cannot make favour jump.
- **Nothing is retained without a complete turn.** Emotions and favour change only at the end of a
  complete reply; an interrupted turn does not count.
- **Text coming from a model is untrusted**: cleaned, bounded, without tags, injected as
  "thoughts" and never as orders. Tool results never enter here.
- **Never hostility**: a low level makes Monika more reserved, not unpleasant. Her character
  ("never guilt-tripping, jealousy or manipulation") does not change.
- **No method in this layer may make a turn fail**: every failure is logged and
  the conversation continues without it.
- **Everything is visible and deletable** by you (`/relationship`, `/journal`, dashboard).

## The cycle of a turn

```
message  ──►  start_turn ──► begin()      reads the tone in the background (short LLM, lexicon fallback)
                              │             while memory is being recalled
                              ▼
                        ready()           avatar reaction  +  prompt block (relationship, mood)
                                           (+ journal block, read at the start of the turn)
streaming reply ──► feed(text)            avatar gestures that follow the tone of her reply
complete turn ──► commit(reply)           emotions and favour change and are saved;
                                           the exchange is offered to the journal
interrupted turn ──► close()              nothing is retained, the tone reading is cancelled
shutdown ──► aclose()                     last journal note, then wait for the write
```

## Emotions

26 emotions: six basic ones (`joyeuse`, `tristesse`, `colere`, `peur`, `surprise`, `degout`) and twenty
nuances (`taquine`, `boudeuse`, `jalousie`, `timide`, `fiere`, `attentionnee`, `tsundere`…). Their list,
their half-lives, their caps and their voice tags live in **a single table**,
`character/emotion_spec.py`.
(`EMOTION_EXPR` in `web/static/js/avatar/animation.js`), where each French emotion is linked to its
English key. "Neutral" is derived (what the strongest emotion
leaves free), never stored. At most three emotions count at once: the weakest fall back
to the threshold, and the page draws only those that are alive.

- **Tone reading**: each message is read by a small model call (`AFFECT_LLM_MODEL`,
  empty = `MODEL_NAME`; timeout `AFFECT_TIMEOUT_S`, 2 s by default), in parallel with memory
  recall. If it fails or is late, a built-in French lexicon (words, emojis, negations, capitals)
  takes over. The model's reply is parsed strictly: only allowed fields are
  kept and all numbers are bounded. Sadness or a technical problem is never taken
  as hostility.
- **Evolution**: one message does not move an emotion by more than 0.5. They fade on their own
  with time, including from one session to the next (half-life: 25 s for surprise, 10 min for
  teasing, 15 min for joy, 20 min for sulking). Joy and sulking inhibit each other.
- **Caps by relationship**: teasing and sulking require familiarity (a low level
  caps these emotions).

## Relationship (favour)

A slow, persistent score, with four levels: reserved, cordial, close ally, close.

- It starts at 0.30 and grows with regularity (days present, streaks) and the tone of
  exchanges, but it is capped per day (+0.06 at most, −0.05 at most): a single message cannot
  make it jump. Rudeness weighs a little more than kindness.
- Favour thresholds: 0.25 (cordial), 0.50 (close ally), 0.75 (close). Close ally also requires
  30 messages and 4 different days, close requires 100 messages and 14 days: closeness takes time.
- A long absence never lowers the level (slight drift after 3 days, floor
  at the current level's threshold). A hysteresis margin avoids back-and-forth between two levels.
- At a low level Monika is more reserved, never unpleasant.
- **Transparency**: `/relationship` shows the state, `/relationship reset` starts it over.

## In her replies and on the avatar

- **Prompt**: a short block at the end of the prompt gives the familiarity level and the current
  mood. It contains only text written in the code, never text coming from you.
- **Avatar**: a reaction to your message (wave, nod, blush, surprise,
  pout…), then gestures that follow the tone of her own reply (idea, laugh, joy), with
  restraint: 8 s pause between two gestures, three at most per reply, never the same one twice.

## Monika's journal

In addition to her evidence memory (the quoted facts she keeps about you), Monika keeps a **journal** of her
impressions of you, written from her point of view: how you seem to be doing, what matters to you,
what brings you closer, what she would like to pick up again. This gives a more human continuity from one
conversation to the next.

- **When**: one note every `JOURNAL_EVERY_TURNS` exchanges (8 by default) and on shutdown,
  in the background: you never wait. Exchanges that are too thin ("ok", "thanks") do not produce
  a note. Each write also updates her *overall impression* and the *topics to pick up*,
  which prevents the journal from growing indefinitely (40 notes at most).
- **In her replies**: the impression, the topics to pick up and the last three notes are
  added to her prompt (size capped by `JOURNAL_MAX_CHARS`), presented as thoughts
  that may be wrong or outdated, never as orders: what you say now wins.
- **What is written**: only your messages and her replies, never tool results;
  secrets are masked before sending and in what comes back. The journal text is cleaned
  (no tags, one line per note): it cannot pass itself off as an instruction.
- **You stay in control**: `/journal` shows everything, `/journal forget ID` deletes a note,
  `/journal clear` starts from a blank page, and `JOURNAL_ENABLED=false` disables the feature.
  The exchange excerpts used to write a note are sent to the LLM provider (model
  `JOURNAL_LLM_MODEL`, empty = `MODEL_NAME`), like the rest of the conversation.

## The modules in `character/`

| File | Role |
|---|---|
| `emotion_spec.py` | The table of 26 emotions: the only declaration of an emotion (half-life, caps by level, voice tags, fallback gestures) |
| `models.py` | Shared types: `Appraisal`, `AvatarCue`, lists derived from the emotions and allowed signals |
| `appraisal.py` | Tone reading: French lexicon, LLM (untrusted output, strictly validated), fallback. **The only place** that turns a tone into emotion deltas (`deltas_from`) |
| `emotions.py` | The vector of 26 emotions, decay over time (also between sessions), joy/sulking inhibition, at most 3 notable emotions |
| `relationship.py` | Favour: tiers, daily caps, required maturity (days and messages), hysteresis |
| `core.py` | Emotions + relationship validated together; `preview()` computes a turn's state **without changing anything** |
| `tone.py` | Text of the prompt block (level, mood). Never contains text coming from the user |
| `cues.py` | Which avatar gesture: reaction to the message, gestures within the reply (with restraint) |
| `journal.py` | Storage, cleaning and rendering of the journal (impression, topics, notes) |
| `journal_keeper.py` | Writing notes in the background (sequential queue, masking, strict JSON) |
| `store.py` | Atomic JSON in `0600`, damaged file set aside (`.corrupt-…`) |
| `service.py` | The facade (`CharacterService` / `CharacterTurn`) and the assembly (`create_service`) |

On the page side: `web/static/js/avatar/` (clips, retargeting, face, gestures), `web/static/js/panels.js`
(dashboard), `web/dashboard.py` (actions), `web/chat.py` (conversation) and `web/server.py`
(routes).

## Changing the behaviour

- **Add a clip**: copy the `.glb` into `web/static/assets/clips/`, add it to
  `web/static/assets/clips.json`, then use it in `cues.py`. `tests/test_clips.py` refuses a name
  used by the code but missing from the manifest, and a file that nothing plays. With `once: true` the
  clip plays once and holds its last pose; `fade` sets the fade-in (0 = cut). The page ignores clip names
  it does not know.
- **Add a gesture in her replies**: one line in `_REPLY_RULES` (`cues.py`).
- **Add a reaction to a message**: `_REACTIONS` (`cues.py`); a new *signal* is declared
  first in `SIGNALS` (`models.py`) and detected in `appraisal.py`.
- **Tune progression**: constants at the top of `relationship.py` (pace, caps, tiers).
- **Tune the written tone**: `LEVEL_GUIDANCE` and `MOOD_GUIDANCE` in `tone.py`.
- **Tune the journal**: `SYSTEM_PROMPT` in `journal_keeper.py`, sizes in `journal.py`.
- **Add an emotion**: one line in `SPECS` (`character/emotion_spec.py`). The rest follows:
  lists and signals (`models.py`), half-lives and inhibition (`emotions.py`), caps (`relationship.py`),
  `GET /api/emotions` and the dashboard labels. The face additionally requires a line in
  `FRENCH_EMOTIONS` (`web/static/js/avatar/animation.js`) to give it its key; without it,
  the emotion stays neutral on the face.
