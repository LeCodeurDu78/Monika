// AudioWorklet: microphone Float32 (the browser's own rate) -> 16 kHz Int16, 512 samples per message.
// 512 samples is the frame size the server's voice activity detector expects (see voice/audio.py),
// so the page feeds the very same pipeline as `parec`. Linear interpolation is enough for speech.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 512;

class MonikaCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;  // `sampleRate` is global in the worklet scope
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.carry = 0;  // position between two input samples, kept from one block to the next
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || !channel.length) return true;
    let position = this.carry;
    while (position < channel.length) {
      const index = Math.floor(position);
      const weight = position - index;
      const first = channel[index] ?? 0;
      const second = channel[index + 1] ?? first;
      const value = first + (second - first) * weight;
      this.frame[this.filled] = Math.max(-1, Math.min(1, value)) * 32767;
      this.filled += 1;
      if (this.filled === FRAME_SAMPLES) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(FRAME_SAMPLES);
        this.filled = 0;
      }
      position += this.ratio;
    }
    this.carry = position - channel.length;
    return true;
  }
}

registerProcessor('monika-capture', MonikaCapture);
