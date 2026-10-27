"""Audio for the emulated watch microphone: 16 kHz mono signed 16-bit PCM, as the watch's microphone
captures it."""

import array
import math
import random
import sys
import wave

SAMPLE_RATE = 16000


def _to_le_bytes(samples):
    if sys.byteorder != 'little':
        samples = array.array('h', samples)
        samples.byteswap()
    return samples.tobytes()


def tone(frequency, amplitude):
    """An endless sine wave, in blocks of samples."""
    phase = 0.0
    step = 2 * math.pi * frequency / SAMPLE_RATE
    while True:
        block = array.array('h', bytes(2 * 320))
        for i in range(len(block)):
            block[i] = int(amplitude * math.sin(phase))
            phase += step
        phase %= 2 * math.pi
        yield _to_le_bytes(block)


def noise(amplitude):
    """Endless white noise, in blocks of samples."""
    while True:
        yield _to_le_bytes(array.array('h', (int(random.uniform(-amplitude, amplitude)) for _ in range(320))))


def read_wav(path):
    """A WAV file's PCM, mixed down to mono and resampled to 16 kHz. Raises ValueError for a file it
    can't read."""
    try:
        with wave.open(path, 'rb') as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            data = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        raise ValueError("{} isn't a PCM WAV file: {}".format(path, e))
    if width == 1:
        values = [b - 128 << 8 for b in data]
    elif width in (2, 3, 4):
        values = [int.from_bytes(data[i:i + width], 'little', signed=True) >> (8 * (width - 2))
                  for i in range(0, len(data), width)]
    else:
        raise ValueError("{} has {}-byte samples; only 8, 16, 24 and 32-bit PCM is supported".format(
            path, width))
    mono = [sum(values[i:i + channels]) // channels for i in range(0, len(values), channels)]
    if rate != SAMPLE_RATE and mono:
        # Linear interpolation: plenty for testing an app with speech or tones.
        ratio = rate / SAMPLE_RATE
        count = int(len(mono) / ratio)
        last = len(mono) - 1
        resampled = []
        for i in range(count):
            position = i * ratio
            left = int(position)
            right = min(left + 1, last)
            resampled.append(int(mono[left] + (mono[right] - mono[left]) * (position - left)))
        mono = resampled
    return _to_le_bytes(array.array('h', mono))


def blocks(pcm, loop):
    """A WAV file's PCM in blocks, once or forever."""
    block = 2 * 320
    while True:
        for i in range(0, len(pcm), block):
            yield pcm[i:i + block]
        if not loop or not pcm:
            return



def live():
    """The computer's default microphone, in blocks, until interrupted. Raises ValueError without
    the sounddevice package or an input device."""
    try:
        import sounddevice
    except (ImportError, OSError) as e:
        raise ValueError("Recording from your microphone needs pebble-tool's mic extra "
                         "(e.g. uv tool install 'pebble-tool[mic]'), and PortAudio on Linux: {}".format(e))
    try:
        stream = sounddevice.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16',
                                            blocksize=320)
    except sounddevice.PortAudioError as e:
        raise ValueError("Couldn't open your microphone: {}".format(e))
    return _record(stream)


# A second of exact zeros: a denied or muted input rather than a quiet room.
SILENT_BLOCKS = 50


def _record(stream):
    silent = 0
    with stream:
        while True:
            data, _ = stream.read(320)
            block = array.array('h', bytes(data))
            if silent < SILENT_BLOCKS:
                silent = silent + 1 if not any(block) else SILENT_BLOCKS + 1
                if silent == SILENT_BLOCKS:
                    sys.stderr.write("Your microphone is recording pure silence. On macOS, allow your terminal "
                                     "to use it in System Settings > Privacy & Security > Microphone.\n")
            yield _to_le_bytes(block)
