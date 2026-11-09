# Voice: listening and speaking

## Installation

```bash
pip install -r requirements.txt   # no voice model here (see "The GPT-SoVITS voice")
```

`sounddevice` (through PortAudio) is the microphone and the audio output, and works on Linux and
Windows. Speech recognition uses **faster-whisper**, running locally on the CPU; the model
(`STT_MODEL`, `small` by default) is downloaded on first use into
`~/.config/monika/voice/models/whisper`.

Monika loads no voice model for synthesis: she talks to **GPT-SoVITS**, started separately. If
PortAudio or the audio output is missing, Monika says so at startup and continues without voice.

On Linux, install PortAudio (`sudo apt install libportaudio2` on Debian/Ubuntu, or
`sudo pacman -S portaudio` on Arch); on Windows it comes with the `sounddevice` wheel. List the
devices with `python -m sounddevice`.

## Listening (`--mode voix`)

- **Microphone**: `sounddevice` reads the default input at 16 kHz mono. Another device:
  `python -m sounddevice`, then `AUDIO_INPUT_DEVICE=<index or name>`. A missing or refused
  microphone is reported immediately, before voice is even touched.
- **End of sentence (VAD)**: the Silero model built into faster-whisper analyses 32 ms frames; speech
  starts after a few confident frames (clicks are ignored) and the sentence ends after
  `VAD_END_SILENCE_MS` (700 ms) of silence, with a little audio kept before and after so words are not
  cut off.
- **Transcription**: faster-whisper (`small`, int8) on the **CPU**, to leave the VRAM for
  synthesis. `STT_LANGUAGE` fixes the spoken language (empty = `LANGUAGE`; `auto` lets Whisper
  guess). A filter removes Whisper's known hallucinations on noise ("Subtitles by…",
  repetitions); discarded sentences trigger nothing.
- **Half-duplex**: Monika does not listen while she is generating her reply or while she is
  speaking (plus `VOICE_RESUME_DELAY_MS`, 400 ms): she does not hear herself. No
  voice interruption, and **no wake word**: she listens continuously. With speakers, headphones remain the safest choice.
- **Quitting**: say a full goodbye sentence ("goodbye Monika", "quit", "see you
  later"), or press Ctrl+C. A sentence that contains "quit" in the middle closes nothing.

## Speaking

The voice is **GPT-SoVITS**, which runs in its own program: Monika sends it text through
its HTTP API (`api_v2.py`), receives a WAV and plays it through `sounddevice`. Monika speaks
**sentence by sentence while the model is still writing**.

- **Modes**: Monika speaks in voice mode and in text mode (`TTS_TEXT_MODE=false` makes her silent
  in text mode). The screen keeps the text as it is; the voice receives a cleaned version: no more
  Markdown, lists, headings or emojis; links keep their text, URLs become
  "a link", paths keep their file name; `21°C`, `20 %`, `14:30` are spoken as "21
  degrees", "20 percent", "2:30 pm"; `2 GB`, `512 MB`, `3.4 GHz`, `25 Mb/s` are spoken as "2
  gigabytes", "512 megabytes", "3.4 gigahertz", "25 megabits per second"; a code block is never
  read (Monika says once that she is leaving it on screen).
- **Abbreviations**: only symbols **attached to a number** are spelled out
  (capacities, rates, frequencies: `KB MB GB TB PB EB`, `KiB MiB GiB TiB`, the bits
  `Kb Mb Gb Tb`, `Hz kHz MHz GHz THz`). "the GB", "3 years of GB" or "Go 1.22" stay as they are,
  and the plural follows English usage: "1 gigabyte", "0.5 gigabytes", "2 gigabytes".
  Only the voice is affected: the screen always shows the original text.
- **Interrupting**: Ctrl+C while she is speaking cuts the voice, the session continues; in text
  mode, sending a new message also cuts what was being said.
- **Voice failure** (server stopped, unreadable reference, lost audio output): the message is
  shown, replies stay on screen; after three consecutive failures Monika stops speaking until the next launch.
- **Audio output**: `AUDIO_OUTPUT_DEVICE` (list: `python -m sounddevice`), empty = default.

## The GPT-SoVITS voice

1. **Install and start the server** (inside the project):

   ```bash
   ./scripts/install_gptsovits.sh          # vendor/GPT-SoVITS, pinned commit
   ./scripts/install_gptsovits.sh --models # also the pretrained weights
   ./lancer-monika.sh                      # starts it and checks a real sentence
   ```

   `lancer-monika.sh` starts `api_v2.py` in the background if it is not already running, waits
   for it to listen, synthesizes a real sentence with your voice pack to check it (voice name,
   sample rate, tags, duration), then opens Monika; on exit it stops the server it started.
   `GPT_SOVITS_DIR` overrides the location (default `vendor/GPT-SoVITS`, then
   `~/Applications/GPT-SoVITS`), `GPT_SOVITS_LOG` where to write its log. To run it by hand:

   ```bash
   cd vendor/GPT-SoVITS && .venv/bin/python api_v2.py -a 127.0.0.1 -p 9880
   ```

   Monika connects at startup (`TTS_URL`, `http://127.0.0.1:9880` by default) and sends it
   a short sentence to warm up the model: the first call can take about ten
   seconds, the next ones much less. `api_v2.py` **has no authentication**: keep it on
   `127.0.0.1`, never expose it on the network.
2. **Drop a voice pack** into `~/.config/monika/voice/` (`TTS_SPEAKERS_DIR` for another
   folder), with the GPT-SoVITS structure:

   ```
   ~/.config/monika/voice/my-voice/
     character.json               optional: { "character_id": "my-voice", "name": "…", "language": "Chinese" }
     references/emotions.json     { "neutral1": { "audio": "neutral1.wav", "text": "reference transcript" }, … }
     references/neutral1.wav      one recording per tag (happy1.wav, sad2.wav, angry3.wav…)
   ```

   Copy the `references/` folder from a GPT-SoVITS engine pack (the `.ckpt`/`.pth` weights are not
   needed: only the server loads them). Several folders in the root = choose one
   with `TTS_CHARACTER_ID` (the folder name or the `character_id`). The paths sent to the server
   are **local**: both programs must run on the same machine.
3. **Language**: GPT-SoVITS has no French front-end. `TTS_LANGUAGE=en` (default) makes the
   Latin text be read by the English front-end; `auto`, `zh`, `ja`, `ko` and `yue` are also accepted, and
   `fr` is treated as `en`. The reference voice must exist in the chosen language:
   `TTS_PROMPT_LANG` (default `en`) describes the pack's transcripts, and a warning appears
   when the pack declares another language. Without `character.json`, the pack must therefore be
   English for `LANGUAGE=en`, French for `LANGUAGE=fr`.

- **Emotion tags**: Monika's emotion becomes `【happy2】`-like in the text, with at most
  two tags per sentence (a transition starts neutral and reaches the emotion in the middle). Each run
  of text is synthesized with **its** reference: a tag change in the middle of a block costs
  only one extra request. `TTS_EMOTION_TAGS=auto` uses **only** tags that have a file
  (`neutral1` otherwise): the voice never pretends to feel an emotion it has no
  recording for. `on` forces every tag in the table, `off` always speaks neutral.
- **Sample rate**: the one announced by the warm-up WAV (32000 Hz for GPT-SoVITS). If the
  server later answers at another rate, Monika resamples: the sound is neither too fast
  nor too slow.
- **Latency**: one sentence per request, so the first sound arrives quickly.
  `TTS_TIMEOUT_S` bounds the wait for each sentence.
- **Pauses**: `TTS_PAUSE_STYLE=neutral` (`neutre` is also accepted; default) changes nothing, sentences follow one
  another as before. `naturel` inserts a short silence **after** each sentence, based on its punctuation
  ("." 0.50 s, "?" 0.45, "!" 0.35, "…" 0.75, a final comma 0.25) and its emotion:
  a sad or disappointed voice lingers (×1.35), an angry or excited one hurries (×0.65 to 0.75), and
  the intensity of the feeling modulates the gap. `TTS_PAUSE_SCALE` multiplies everything, `TTS_PAUSE_MAX_S` caps a pause
  (1.2 s by default). These are **silences only**, never hesitation noises: no "uh" is
  added. Nothing is inserted before the first word of a reply, nor after its last — speech starts
  and flows without waiting for it. Adjustable live from `/settings` (group *Voice*). The rule lives in `voice/pauses.py`.
- **Failure**: server stopped or slow → clear message, replies on screen, and the conversation
  continues; three consecutive failures cut the voice.

## Talking to Monika in the browser

```text
[ 🎙 Talk ]  →  browser microphone  →  AudioWorklet 16 kHz  →  VAD + Whisper (the same ones)
                                                            ↓
           device speakers  ←  WebAudio  ←  GPT-SoVITS synthesis (the same voice)
```

- **Same pipeline as voice mode**: the page adds neither a second speech detector nor a second
  Whisper. An AudioWorklet (in `web/static/js/voice.js`) brings the microphone down to 16 kHz mono and
  sends it in frames of 512 samples over the WebSocket; they enter the same `VoiceListener` as
  `--mode voix` (where the microphone comes from `sounddevice`). The local audio backend is not
  touched: the out-loud conversation on the machine keeps working.
- **The reply is heard**: when the page microphone is open, the `Speaker` writes into the page
  (`WebAudio`, `web/voice.py`) instead of the local output; closing the microphone returns the
  voice to the machine's output. The pauses between sentences (see *Pauses* above) are of course heard here too, since it is the same PCM stream.
- **One voice at a time**: a second device that opens the microphone takes over the audio stream; the
  first hears nothing until it takes it back. Closing the page closes the microphone (the
  WebSocket dropping cuts listening on the server side).
- **Permissions, cleanly**: a refusal, a missing microphone, a microphone already taken by another
  program or a browser without `getUserMedia` each show a clear sentence under the
  conversation, and **the button stays idle**: nothing is sent to the server, no session is
  opened.
- **HTTPS required (or localhost)**: browsers only give the microphone to a secure origin.
  `http://localhost:8765` works; a page opened from another device over
  `http://` **does not** — hence `tailscale serve` (HTTPS) for the phone. The message says so instead
  of failing silently.
- **Half duplex**: the microphone reopens when the reply has finished being **heard**, not when it has
  finished being sent (the server holds the playback clock); the browser's echo cancellation
  does the rest on an open speaker.
- **Limits**: no wake word, no barge-in (talking over Monika does not cut her), and
  voice in the page requires working synthesis — without GPT-SoVITS, the microphone still works
  but replies stay on screen.
