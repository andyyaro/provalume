"""Cut "Baileys" (Diego Nava, Mixkit 476, Mixkit Stock Music Free License: assets/audio/SOURCE.md) to the Provalume film.

    python3 -I sound/music.py        # -> assets/audio/music.wav (48 kHz stereo), sound/grid.json

The brief, 2026-11-01: "use another track ... calm but not too calm" (v1 reused "Peace", the Orkestra film's track).
Baileys is smooth contemporary R&B, 95 BPM: a bar is 2.5263 s and track bar n starts at 2.5612 + 2.5263 n s.
- Track bars 0-2 are the soft intro (after a 2.56 s pickup); the groove enters on bar 3 (10.14 s).
- Groove to bar 14; a breakdown on bars 15-18; the second groove on bars 19-34 (phrases start on 19, 23, 27, 31).
- Bar 35 (90.98 s) is the outro: the groove stops and the track decays to silence by about 96 s.

The edit (each join is on a bar line with a 12 ms crossfade):
  film  0.000- 5.087  track  0.000- 5.087   pickup + intro bar 0
  film  5.087- 7.613  track  2.561- 5.087   intro bar 0 again, so the groove lands on the film's drop
  film  7.613-12.665  track  5.087-10.140   intro bars 1-2
  film 12.665-42.981  track 10.140-40.456   the groove enters on the drop ("Provalume keeps what was proved.")
  film 42.981-55.613  track 60.666-73.298   the breakdown is skipped: the second groove, bars 23-27
  film 55.613-62.500  track 90.982-end      the outro under the end card, decaying out
"""
import json, subprocess
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TRACK = ROOT / "sound/track.mp3"            # Mixkit 476; see assets/audio/SOURCE.md
SR = 48000
B0, BAR = 2.5612, 4 * 60 / 95.0
tb = lambda n: B0 + n * BAR                      # track bar n -> track seconds
SEGMENTS = [  # (film start, track start, track end)
    (0.0, 0.0, tb(1)),
    (tb(1), tb(0), tb(1)),
    (tb(2), tb(1), tb(3)),
    (tb(4), tb(3), tb(15)),
    (tb(16), tb(23), tb(28)),
    (tb(21), tb(35), None),
]
DROP, END_CARD = tb(4), tb(21)                   # 12.665 s, 55.613 s in film time
FILM_END = 62.5
XF = 0.012


def main():
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(TRACK), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    track = np.frombuffer(raw, np.float32).reshape(-1, 2).T.astype(np.float64)
    n = int(FILM_END * SR)
    out = np.zeros((2, n))
    x = int(XF * SR); ramp = np.linspace(0, 1, x)
    for k, (f0s, t0s, t1s) in enumerate(SEGMENTS):
        f0 = int(round(f0s * SR))
        f1 = int(round(SEGMENTS[k + 1][0] * SR)) if k + 1 < len(SEGMENTS) else n
        t0 = int(round(t0s * SR))
        seg = track[:, t0: t0 + (f1 - f0)]
        seg = np.pad(seg, ((0, 0), (0, (f1 - f0) - seg.shape[1])))
        if k > 0 and t0 >= x:   # crossfade into the new piece across the join
            out[:, f0 - x: f0] = out[:, f0 - x: f0] * (1 - ramp) + track[:, t0 - x: t0] * ramp
        out[:, f0:f1] = seg
    fi = int(0.3 * SR); out[:, :fi] *= np.linspace(0, 1, fi)
    fo = int(0.8 * SR); out[:, -fo:] *= np.linspace(1, 0, fo)
    import soundfile as sf
    (ROOT / "assets/audio").mkdir(parents=True, exist_ok=True)
    sf.write(ROOT / "assets/audio/music.wav", out.T.astype(np.float32), SR)
    grid = {"track": "Baileys (Diego Nava, Mixkit 476)", "bpm": 95, "bar": BAR, "drop": round(DROP, 3),
            "end_card": round(END_CARD, 3), "track_ending": round(END_CARD, 3), "duration": FILM_END,
            "segments": [[round(a, 3), round(b, 3), None if c is None else round(c, 3)] for a, b, c in SEGMENTS]}
    (ROOT / "sound/grid.json").write_text(json.dumps(grid, indent=1))
    print(f"music.wav {FILM_END} s; drop at {DROP:.3f} s, end card {END_CARD:.3f} s")


if __name__ == "__main__":
    main()
