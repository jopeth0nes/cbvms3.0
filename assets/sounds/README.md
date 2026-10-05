# CBVMS local notification audio

These original synthetic PCM WAV files were generated for CBVMS by
`scripts/generate_detection_sounds.py`. No recordings, voices, third-party samples,
network services, or OS sound libraries are used. The generated WAV assets are
made available under **CC0-1.0** (public-domain dedication):
https://creativecommons.org/publicdomain/zero/1.0/
The generator is included so the exact assets can be reproduced with Python's
standard library: `python scripts/generate_detection_sounds.py`.

| Asset | Pattern | Duration | Measured peak |
| --- | --- | --- | --- |
| positive.wav | Rising E5–A5 chime | 0.480 s | −15.00 dBFS |
| violation.wav | Descending G4–E♭4 tone | 0.490 s | −16.69 dBFS |
| suspension.wav | Three spaced D5–G5–D5 pulses | 0.690 s | −16.14 dBFS |
| generic.wav | Separate legacy/unknown 880 Hz alert | 0.250 s | −19.17 dBFS |

All assets are mono, 44.1 kHz, 16-bit PCM. Each note has a 25 ms raised-cosine
attack and 70 ms release, with a quiet second harmonic. The generator limits the
combined peak to −15 dBFS without clipping. Files finish with 30 ms of silence.
Digital levels do not establish speaker loudness: listen on the deployment
computer at its usual volume before enabling live sounds.

Keep this directory beside the application's `core` directory. Paths are resolved
from the installed source location, independent of the working directory. Any
future executable/frozen packaging configuration must include `assets/sounds/*`;
this repository currently runs the application from its source tree.
