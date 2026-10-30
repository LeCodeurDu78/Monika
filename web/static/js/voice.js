// Voice in the page: the microphone goes up (getUserMedia + AudioWorklet, 16 kHz PCM) and Monika's
// voice comes back down as PCM played with WebAudio. The terminal's voice mode is untouched: the
// server keeps `parec`/`pacat` for it, this is only another source and sink.

const TARGET_RATE = 16000;
const FRAME_BYTES = 1024;  // 512 samples, the window the server's VAD expects

const MIC_ERRORS = {
  NotAllowedError: 'Micro refusé : autorise-le dans le navigateur (icône à gauche de l’adresse).',
  NotFoundError: 'Aucun micro détecté sur cet appareil.',
  NotReadableError: 'Le micro est déjà pris par un autre programme.',
  OverconstrainedError: 'Le micro ne correspond pas aux réglages demandés.',
  SecurityError: 'Micro bloqué : la page doit être en https ou sur localhost.',
};

export function mountVoice({ status = () => {} } = {}) {
  const button = document.querySelector('#p-chat .mic');
  let on = false;
  let starting = false;
  let mic = null;      // capture AudioContext
  let stream = null;
  let node = null;
  let player = null;   // playback AudioContext, at the voice's own rate
  let nextAt = 0;      // when the next chunk should start
  let sources = new Set();

  const socket = () => {
    const ws = window.monika?.ws;
    return ws && ws.readyState === WebSocket.OPEN ? ws : null;
  };

  function paint() {
    if (!button) return;
    button.classList.toggle('on', on);
    button.textContent = on ? '🎙 Micro ouvert' : '🎙 Parler';
    button.setAttribute('aria-pressed', String(on));
  }

  function send(payload) {
    const ws = socket();
    if (ws) ws.send(JSON.stringify(payload));
  }

  function sendAudio(buffer) {
    const ws = socket();
    if (ws && on) ws.send(buffer);  // dropped when the link is down: stale audio is useless
  }

  /** A chunk from the worklet: 1024 bytes of 16-bit PCM at 16 kHz. */
  function onChunk(buffer) {
    if (buffer.byteLength !== FRAME_BYTES) return;
    sendAudio(buffer);
  }

  async function openMic() {
    if (!navigator.mediaDevices?.getUserMedia) {
      status('Micro indisponible : cette page doit être en https ou sur localhost.');
      return false;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        video: false,
      });
    } catch (err) {
      status(MIC_ERRORS[err?.name] || `Micro indisponible (${err?.name || 'erreur inconnue'}).`);
      stream = null;
      return false;
    }
    mic = new AudioContext();
    try {
      await mic.audioWorklet.addModule('/static/js/voice-worklet.js');
    } catch (err) {
      status('Capture audio impossible : ce navigateur ne charge pas l’AudioWorklet.');
      await closeMic();
      return false;
    }
    node = new AudioWorkletNode(mic, 'monika-capture', {
      numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1,
    });
    node.port.onmessage = (event) => onChunk(event.data);
    const source = mic.createMediaStreamSource(stream);
    const mute = mic.createGain();  // the graph must reach a destination to be pulled: at zero volume
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(mic.destination);
    return true;
  }

  async function closeMic() {
    if (node) {
      node.port.onmessage = null;
      node.disconnect();
      node = null;
    }
    if (stream) {
      for (const track of stream.getTracks()) track.stop();
      stream = null;
    }
    if (mic) {
      await mic.close().catch(() => {});
      mic = null;
    }
  }

  function stopPlayback() {
    for (const source of sources) {
      try { source.stop(); } catch (err) { /* already ended */ }
    }
    sources = new Set();
    nextAt = 0;
  }

  /** PCM from the server: int16 mono at `rate` (the voice engine's own rate). */
  function onAudio(buffer) {
    const ws = window.monika?.ws;
    if (!player || !ws) return;
    const pcm = new Int16Array(buffer);
    if (!pcm.length) return;
    const audio = player.createBuffer(1, pcm.length, player.sampleRate);
    const data = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i += 1) data[i] = pcm[i] / 32768;
    const source = player.createBufferSource();
    source.buffer = audio;
    source.connect(player.destination);
    const start = Math.max(player.currentTime + 0.03, nextAt);
    source.start(start);
    nextAt = start + audio.duration;
    sources.add(source);
    source.onended = () => sources.delete(source);
  }

  function openPlayer(rate) {
    if (player && player.sampleRate === rate) return;
    if (player) player.close().catch(() => {});
    try {
      player = new AudioContext({ sampleRate: rate });
    } catch (err) {
      player = new AudioContext();  // the browser refused that rate: play at its own
    }
    if (player.state === 'suspended') player.resume().catch(() => {});
    nextAt = 0;
  }

  async function start() {
    if (on || starting) return;
    starting = true;
    const ok = await openMic();
    starting = false;
    if (!ok) {
      paint();
      return;
    }
    on = true;
    paint();
    send({ type: 'voice', on: true });
    status('Micro ouvert : parle, je t’écoute.');
  }

  async function stop() {
    if (!on && !stream) return;
    on = false;
    paint();
    send({ type: 'voice', on: false });
    await closeMic();
    stopPlayback();
    status('');
  }

  function pickButton() {
    if (!button) return;
    button.addEventListener('click', () => {
      if (on) stop();
      else start();
    });
  }

  /** One message from the server about the voice (web/voice.py). */
  function onEvent(msg) {
    if (msg.type === 'audio') {
      if (msg.event === 'start') openPlayer(msg.rate || 24000);
      else if (msg.event === 'stop') stopPlayback();
      return;
    }
    if (msg.event === 'on') status('Je t’écoute.');
    else if (msg.event === 'off') {
      if (on) stop();  // the server ended the session (goodbye, error, page reload)
    } else if (msg.event === 'ecoute') status('Je t’écoute…');
    else if (msg.event === 'parole') status('Je t’entends…');
    else if (msg.event === 'transcription') status('Je transcris…');
    else if (msg.event === 'text') status('');
    else if (msg.event === 'error') {
      status(msg.text || 'La voix s’est arrêtée.');
      if (on) stop();
    }
  }

  /** The page lost its server: the microphone must not stay open on a dead link. */
  function onClose() {
    if (!on) return;
    on = false;
    paint();
    closeMic();
    stopPlayback();
    status('Connexion perdue : micro fermé.');
  }

  pickButton();
  paint();
  return { onEvent, onAudio, onClose, start, stop, get on() { return on; } };
}
