"""Deterministic original CBVMS tones; standard library only, no sampled material.

Generated audio is dedicated to the public domain under CC0-1.0.
Run from any directory: python scripts/generate_detection_sounds.py
"""
import math
from pathlib import Path
import struct
import wave

RATE = 44100
TARGET = Path(__file__).resolve().parents[1] / 'assets' / 'sounds'
# (start seconds, duration seconds, fundamental Hz, peak amplitude)
TONES = {
    'positive': [(0, .24, 659.25, .13), (.16, .29, 880, .13)],
    'violation': [(0, .23, 392, .15), (.19, .27, 311.13, .15)],
    'suspension': [(0, .16, 587.33, .17), (.22, .16, 783.99, .17), (.44, .22, 587.33, .17)],
    'generic': [(0, .22, 880, .12)],
}


def generate():
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, tones in TONES.items():
        samples = [0.] * int((max(t+d for t,d,_,_ in tones)+.03) * RATE)
        for start, duration, freq, level in tones:
            count = int(duration * RATE)
            for i in range(count):
                t = i / RATE
                # 25ms raised-cosine attack, 70ms release; gentle second harmonic.
                attack = .5 - .5 * math.cos(math.pi * min(1, t/.025))
                release = .5 - .5 * math.cos(math.pi * min(1, (count-1-i)/RATE/.070))
                tone = (math.sin(2*math.pi*freq*t) + .12*math.sin(4*math.pi*freq*t)) / 1.12
                samples[int(start*RATE)+i] += level * attack * release * tone
        peak = max(abs(v) for v in samples)
        # Peak ceiling -15dBFS, with no hard clipping or runtime volume boost.
        scale = min(1, 10**(-15/20)/peak)
        pcm = b''.join(struct.pack('<h', round(v*scale*32767)) for v in samples)
        with wave.open(str(TARGET / f'{name}.wav'), 'wb') as wav:
            wav.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            wav.writeframes(pcm)
        print(f'{name}: {len(samples)/RATE:.3f}s; peak {20*math.log10(peak*scale):.2f} dBFS')


if __name__ == '__main__':
    generate()
