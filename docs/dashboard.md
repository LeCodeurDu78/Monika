# Avatar, dashboard and settings

At startup, Monika opens a page at `http://localhost:8765` (`WEB_PORT`; `WEB_ENABLED=false`
to turn it off). Two **tabs** at the top: **Chat** (the avatar and the conversation) and **Dashboard**
(context, relationship, memory, sessions, recipes, custom tools); the choice is remembered
from one visit to the next. On a wide screen, the avatar stays on the left and the selected section on the
right; on a phone, each section takes the full width and the avatar fades when the
dashboard is open. The **handle** between the two can be dragged to give more room to the
panel (the width is remembered), adjusted with the keyboard (left/right arrows) and reset to the default
by double-click; its **❯ / ❮ button** collapses or reopens the panel.

- **Avatar**: a VRM model (`web/static/assets/avatar.vrm`) animated by motion clips
  (`web/static/assets/clips/`, manifest `web/static/assets/clips.json`:
  rest, thinking, talking, joy, surprise, nod, wave,
  anger, crying, dance, hands on hips, open arms, pointing, stretching… Movement and
  face go through the engine at `web/static/js/avatar/animation.js`:
  same clip retargeting, same expression weights (joy, anger, sadness, relaxed, surprise), same
  blinking, same lip sync, driven by the state and the actual sound — no home-grown engine on top.
  An indicator shows
  "At rest", "Listening", "Thinking" or "Speaking".
  The set is a literature club room at dusk, entirely procedural
  (`web/static/js/avatar.js`): plank floor, blackboard, latticed windows and
  curtains, desks, a swaying pendant light and floating dust. No file to download;
  if the set fails to build, the scene remains usable without it.
- **Camera**: drag with the left button to orbit around her, the wheel zooms in or out,
  and dragging with the right button up or down slides the focus from her head to her shoes
  ("`?view=full`" frames the whole body at start). The orbit never goes under the
  floor, nor through the ceiling or walls: when rising or zooming out, the camera stops inside the
  room (the highest angle and maximum distance then depend on her height and the pull-back); it
  follows her breathing without shaking.
  **3D libraries**: three.js and three-vrm are provided in `web/static/vendor/` and served
  locally (no dependency to download: the folder is versioned). Open the page with `?debug`
  to test each clip and each emotion; `?view=full` frames the whole body instead of the bust.
- **Conversation** (first block): this is where you talk to Monika. Type,
  Enter sends (Shift+Enter adds a line), **Stop** cuts the current reply —
  exactly the same stop as `/stop`. Scheduled reminders appear marked as such, and
  the reply is written into the page as it goes. The last twenty lines are
  kept in memory for a reload (the durable part is the *Sessions* block); a turn started
  out loud (`--mode voix`) also appears here. One conversation at a time:
  sending during a reply is refused (`409`) rather than queued. **Commands**
  (`/help`, `/sessions`, `/resume ID`, `/remind …`, `/stop`…) are typed there too: their answer
  appears in the conversation.
- **Talking from the page**: the **🎙 Talk** button opens the browser microphone
  (`getUserMedia`, echo cancellation and noise reduction requested) and plays the reply **here**, through
  headphones or the speakers of the device viewing the page. See
  [Talking to Monika in the browser](voice.md#talking-to-monika-in-the-browser).
- **Dashboard** (second tab), seven blocks: *Conversation*, *Context* (size of the
  conversation), *Relationship* (Monika's level, favour and mood; her journal, where you can
  edit or delete each note, correct or clear the overall impression, or clear everything;
  and "Start over" for the relationship, with confirmation), *Memory* (the quoted facts, with
  their evidence sentence and their state — active or corrected —, each editable or deletable) and *Sessions* (the last
  fifteen saved conversations, the current session marked, with **Resume** and
  **Delete**),
  *Recipes* (learned procedures: draft to keep, active version, usages) and *Custom
  tools* (the MCP servers Monika wrote for herself: name, state — active, disabled or
  refused —, code fingerprint and description; code changed since verification is
  flagged).
  Deleting the current session starts a new conversation; both actions are refused
  during a reply, so the history is not swapped under the feet of a turn in progress.
- **Phone and remote access**: the page is responsive (sticky tabs, touch areas, notch safe
  areas, natural scrolling on mobile). It only listens on `127.0.0.1`:
  for the phone, go through Tailscale, see [From your phone](#from-your-phone-tailscale).
  Another device on the local network requires `WEB_HOST=0.0.0.0` **and**
  `WEB_TOKEN` (without a token, the page is open to whoever reaches the port); see
  [Access to the page](security.md#access-to-the-page).
- **WebGL** is required in the browser; without it, the page says so and the dashboard
  still works.

## From your phone (Tailscale)

Monika stays on `127.0.0.1`: `tailscale serve` only proxies `http://127.0.0.1`, terminates
HTTPS and talks to the page. Nothing to open on the local network, token unchanged (`WEB_TOKEN` in `.env`).

```bash
python -m monika --telephone   # tailnet name, command and link to open
tailscale serve --bg 8765      # on this machine, once (Monika running)
```

Then open on the phone (connected to the same tailnet) the displayed link,
`https://<machine>.<tailnet>.ts.net/?token=<token>`: the token becomes a cookie (HttpOnly, SameSite
strict, `Secure` over HTTPS) and the address becomes clean again. `tailscale serve status` checks the
configuration, `tailscale serve reset` removes it. **Not `tailscale funnel`**: that would be the whole
internet. HTTPS is also what gives the phone the microphone.

## Settings page (`/settings`)

A "Settings" link in the header opens this page (same server). It has three blocks:

- **Health**: seven indicators — LLM, speech recognition, GPT-SoVITS voice, MCP servers, fast
  memory, documents, local page. Each probe is bounded to 1.5 s and launched in parallel; the
  refresh happens every 5 s **only if the tab is visible**, plus a
  "Test now" button. A **disabled** component is grey, not red; a failed component is
  red with the detail (for example "127.0.0.1:9880 unreachable"). The LLM is probed through its model
  list, never through a completion (that would cost a call); voice through a TCP connection.
- **Settings**: a readable, grouped selection (model, behaviour, character, roles, memory,
  documents, voice, page). Keys defined in `.env` are **greyed out and marked**: `.env`
  keeps precedence, the page does not pretend to control them, and the API **refuses** them (400)
  even when called by hand. "Save" validates then writes the file named by
  `MONIKA_SETTINGS_FILE` (default `<MONIKA_HOME>/settings.json`; the installer uses
  `config/settings.json`) in `0600`, atomic write, and **applies live** what can
  be applied (temperature, `MAX_STEPS`, thresholds, roles, `PERSONA_TEXT`…); a banner lists the keys that
  require a restart (port, URL, models, devices, index). **All or nothing**: an out-of-range
  value is refused with a message and writes nothing.
- **Provider and language**: a selector applies OpenRouter or Ollama (address, model, key),
  and another switches the whole interface between French and English immediately. Both write
  their value to `.env`, because `.env` has priority; the provider change asks for a restart,
  the language does not.
- **MCP reload**: `mcp_servers.json` is watched. An edit closes the sessions, re-reads the file
  and reconnects within a couple of seconds, without restarting Monika.
- **API key**: the page only says "set" or "not set" and never shows the
  value. Saving replaces the single `OPENAI_API_KEY` line in the project's `.env` (one line, without
  a newline, file in `0600`); restart required.

What the page **cannot** change: `MONIKA_HOME`, the indexed folders (`RAG_ROOTS`), the
MCP servers file and the voice folder — writing a path from the browser would make the
page a launcher for arbitrary files. These keys remain editable by hand in `.env`.

Like the rest of the page, `/api/settings` and `/api/health` open **without a token** (the first
contains the persona and the model names); writes go through the same `act()` as the
dashboard. If `settings.json` is
damaged by hand **while** Monika is running, writing it from the page fails with a clear message:
fix it or delete it. At **startup**, a `settings.json` that cannot load (broken JSON or
out-of-range value) no longer blocks anything: it is renamed to `settings.json.corrupt-<date>`, a
warning says why, and Monika starts again from the default values.
