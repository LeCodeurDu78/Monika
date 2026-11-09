# Agent, context and prompt

On every message, Monika reasons, calls tools (function calling), observes, starts again,
then replies, at most `MAX_STEPS` (100) model calls per turn. She acts directly, without
asking for confirmation, and without claiming anything without evidence.

- **Loops**: same tool + same arguments + same result. Warning on the 3rd time
  (`LOOP_WARN_AFTER`), stop with a review on the 5th (`LOOP_STOP_AFTER`).
- **Honest review**: if the step limit or a loop stops the task, Monika says what is
  done, what is not, and what remains.
- **Untrusted content**: every tool result reaches the model inside a
  `<donnees_outil>` envelope, presented as data, never as an order. A fake opening or closing tag
  in the content is neutralized.
- **Waiting phrases**: for a slow tool, one phrase after `FILLER_DELAY_S` (1.5 s), then
  every `FILLER_REPEAT_S`, at most `FILLER_MAX_PER_TURN` (2) per turn. Displayed, and spoken
  out loud when voice is loaded.
- **Four roles** (`python -m monika --roles` shows them): **surface** (the opening line,
  before the tools; `SURFACE_LLM_MODEL`, empty = disabled), **deep** (the full turn: reply,
  tools, review, `MODEL_NAME`), **reflection** (outside the turn: memory director, context
  summaries, nightly review; `REFLECTION_MODEL`, empty = the main model) and **action** (the
  avatar's gestures, `ACTION_LLM_MODEL`). Nothing from reflection is awaited during a reply:
  a cheaper model there therefore has no effect on the turn's speed.
- **Surface then deep**: when `SURFACE_LLM_MODEL` is set, a small model first writes
  **one** short sentence, displayed immediately; the full loop then takes the turn with the
  tools and receives that sentence so it does not repeat it. Speech therefore comes **before** the tools:
  the surface sees neither the tools nor their results and cannot announce any fact, number or
  path. If it exceeds `SURFACE_TIMEOUT_S`, the line is simply omitted and the turn continues.
  `SURFACE_PROMPT` adds instructions to that role alone.
- **Reflection between turns** (`REFLECTION_ENABLED=true`, disabled by default): after a successful
  turn, the reflection role prepares a short piece of advice in the background, injected into the next turn as
  an internal note. With `REFLECTION_SPEAK=true`, it can also trigger speech: an automatic
  turn labelled "Reflection", with the usual tools and review. `REFLECTION_PROMPT`
  adds instructions to that role. Everything is set in `/settings`, group *Roles*.
- **Action role (optional, disabled by default)**: after the turn, in the background, it chooses
  a short chain of gestures (2 to 8 s each, three at most) that the page plays if no gesture is
  in progress; a following message **cancels** the still-pending chain, in the agent and on the
  page, so a gesture decided for one reply never lands during the next. If it fails, the deterministic
  gestures from `character/cues.py` remain the only active ones. This role costs one model
  call per turn: enable it while measuring.
  All tools are sent on every request, with no intermediate planner.
- **Ctrl+C** cancels the current tool. If actions had already been taken, a note
  reminds the model of them so it does not believe they were cancelled.
- **Prompt** rebuilt on every turn: personality, working method, "data, not
  orders" rule, date in French and system context (OS, user, working folder).
- **Large results**: beyond `TOOL_RESULT_MAX_CHARS` (20,000), the result is truncated
  (start + end) and the full text is saved in `~/.config/monika/tool_results`
  (private files, purged after `TOOL_RESULTS_KEEP_DAYS` = 14 days). The model reads the rest
  with `lire_resultat(id, debut, longueur)`.
- **Context budget**: the size is estimated before every call (about 3.3 characters per
  token: an estimate, not an exact count). Above `CONTEXT_BUDGET_TOKENS`
  (150,000, well below the 1 M window), older exchanges are summarized by the
  model; at least `CONTEXT_KEEP_RECENT_TOKENS` recent tokens are kept, with a cut always
  between two turns, never in the middle of a tool call. If a single turn is huge, its oldest
  tool results are removed (the last 3 stay). If summarization fails, you are told
  and the conversation continues without compaction.

## Learned recipes

After a **successful turn** (complete reply, no tool error) with at least
`RECIPES_MIN_TOOLS` (3) tool calls, a background task asks the model for a **parameterized
recipe**: goal, parameters, steps with the tools used, final check. It is written as a
**draft** in `recipes.json` — nothing is saved without your approval.

- `/recipes` lists the drafts (with their id), the active versions and the usages;
  `/recipes keep ID` saves it, `/recipes refuse ID` throws the draft away,
  `/recipes forget ID` deletes the recipe, its history and its counters.
- On the next turn, an active recipe that overlaps the request (significant words from the title, the
  goal and the parameters) is injected into the prompt under "Recipes", presented as
  **data**: a procedure that already succeeded, to adapt, never an order. At most
  `RECIPES_INJECT_MAX` (2) recipes per turn.
- Each injection counts one **usage**; the following turn records a success (finished
  normally, no tool error) or a failure.
- If the turn improves an existing recipe, the keeper proposes a **new version** under
  the same id: it is applied only after `/recipes keep ID`, and the old one goes into
  the history (10 versions kept at most).
- The **Recipes** panel of the dashboard shows the state (draft, active version, proposed
  version) and the usages. Texts are masked before writing.

What it does not do: no recipe is applied automatically, nothing is shared
between machines, and matching is **lexical** (no embeddings): a request worded
very differently may trigger no recipe. The keeper costs one model call per
eligible turn (`RECIPES_ENABLED=false` to turn it off).

## Sessions and resuming

With `SESSIONS_ENABLED=true` (disabled by default), each conversation is saved in
`~/.config/monika/sessions/sessions.db`: SQLite with **FTS5 full-text search**, file in
`0600`. One row per session, one per turn, one per tool call, plus the history as the
model saw it — that snapshot is what makes `/resume` exact, tool calls included.

- **Secrets masked** before writing, field by field (`security/masking.py`): a key pasted
  into a message or a tool argument never reaches disk.
- **Interrupted turn** (Ctrl+C, model error, window closed): written with the
  `interrupted` status; a turn left open by a brutal stop is marked that way at the next launch.
- **Context budget**: `/resume` does not reinject everything. Beyond the budget, the oldest turns
  are dropped **whole** (never in the middle of a tool call) and the resumption says so.
- **Retention**: sessions unused for `SESSIONS_KEEP_DAYS` (30 days) are purged at
  startup. `/clear` starts a fresh session without erasing the old one.
- **You stay in control**: `/sessions`, `/search`, `/session forget ID`, and the *Sessions* panel of the
  dashboard list the last fifteen, let you resume them and delete them.
  Resuming from the page reloads the conversation **in the running process**: it continues in the page
  (*Conversation* block).
- **Search**: the index covers what was said (message and reply), not tool
  results: those stay in the history but outside the index.
- **Limit**: the full history is rewritten on every turn (a few hundred kilobytes on
  a very long session); writing is atomic per transaction, and a database failure is
  logged without ever interrupting the conversation.

## Scheduled tasks

With `TASKS_ENABLED=true`, Monika remembers to do something later — and does it
herself: a reminder is not an alarm, it is a turn like any other, with its tools and its
voice.

```
/remind in 20 minutes: drink water
/remind tomorrow 8am: check the weather and tell me what to wear
/remind every 2 hours: remind me to take a break
/remind every day at 8am: give me today's schedule
/remind every Monday at 9am: sum up the week ahead
/remind Monday to Friday at 9am: the morning review
```

- **What she accepts**: `in N minutes|hours|days`, a time (`6:30pm`, `08:05` — tomorrow if
  it has already passed), `tomorrow 8am`, and repetition: `every 2 hours`, `every 3 days`,
  `every 30 minutes`, `every day at 8am` (or `every day at 08:30`), and days of the
  week: `every Monday at 9am`, `every Monday and Thursday at 9am`, `every Tuesday, Wednesday and
  Friday at 2:30pm`, `Monday to Friday at 9am`. The last two keep a **fixed time**:
  it does not drift by 24 hours from the creation time. Natural language also works:
  "remind me in two hours to take out the bins" goes through the `taches_planifier` tool.

- **What she does at the appointed time**: a full turn, without you in front of the screen. The reply
  appears **in the page** (and is spoken out loud if voice is active),
  and is saved in the session.
- **A process must be running**: `python -m monika`, left open (the instance lock prevents
  launching a second one on the same data folder).
  What fell due while she was stopped triggers **at the next launch**, saying how late it is
  ("reminder due at 8:00, it is 9:20"). A reminder that comes back every hour does not replay
  missed hours: it starts again from now.
- **You stay in control**: `/tasks` lists them (time, repetition, id), `/task cancel ID` deletes one.
  A task is never created without you: neither the model nor the code invents one.
- **Limits**: 50 tasks (`TASKS_MAX`), a text of 500 characters, and nothing fires
  while a turn is in progress — the reminder waits for the current reply to finish. A daily or
  weekly task missed (machine off) starts again at the next occurrence, without
  catch-up; a multi-day task resumes on the next day of its list.

## Morning briefing

With `BRIEFING_ENABLED=true`, Monika gives you a briefing every morning — weather, calendar,
important mail, the day's tasks — without you asking anything. It is a **full turn** at the appointed
time: she writes it in her voice, the reply appears in the page (and is spoken out loud if voice is
active) and is saved in the session like any other exchange. The page shows the label
**Morning briefing**, never the report or the prepared request.

- **What she reads**: the day's weather (Open-Meteo, **no key**), the scheduled tasks that
  fall today (`/remind`), then your calendar and your mail **through her MCP tools**: connect a
  calendar server and a mail server, and she uses them herself. A missing tool is said to be
  missing in half a sentence; nothing is invented.
- **Settings**: `BRIEFING_TIME` (07:30 by default), `BRIEFING_DAYS` (`all`, or
  `monday,tuesday,wednesday,thursday,friday`), `BRIEFING_CITY` (the weather city; empty = no
  weather) and `BRIEFING_SECTIONS` (`weather,calendar,mail,tasks`: remove what you do not want). They
  are also set in `/settings` (group *Morning briefing*).
- **Right now rather than 7:30**: `/briefing`. She does it immediately and will not do it again the same day.
- **Catch-up**: a missed briefing (machine off) is given at the next launch if it is less than 4 h
  late; after that, it starts again the following morning, without catching up on past days.
- **A process must be running**: `python -m monika`, left open.
  Nothing fires while a turn is in progress: the briefing waits for the reply to finish.
- **Limits**: the weather comes from an external service (Open-Meteo) — without a network, it is said to be
  unavailable; the calendar and mail exist only if the corresponding MCP servers are connected; there is no
  dedicated model, so the briefing counts as a turn, with its tool calls.
