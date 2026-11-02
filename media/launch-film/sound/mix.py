"""The Provalume soundtrack: Kokoro af_heart, "Baileys" (Diego Nava) as a quiet bed, and a few soft effects.

    python3 -I sound/mix.py

Inputs: assets/audio/music.wav (sound/music.py), narration/chosen/<id>.wav + narration/words.json (narration/pick.py),
sound/sfx-src/mixkit-<id>.mp3 (Mixkit free sound effects, same licence as the music).
Outputs:
- assets/audio/mix.wav: the soundtrack, about -16 LUFS, true peak under -1.5 dBTP.
- assets/audio/vo.wav: the voice alone.
- assets/vo.js: window.VO, each line's start/end and word times in film seconds.
- assets/timing.js and sound/timing.json: window.TM, the scene windows. Every scene reads its start from it, and
  build.py writes the same numbers into index.html.

Levels (the brief: "the sound should not be too loud, just present, the voice should be clear"):
- The voice is the loudest thing. The music bed sits about 13.5 dB under it while she speaks, and comes back up by
  4.5 dB between lines.
- Each effect hits 3-6 dB over the bed and is placed so its peak lands one frame after its picture.
"""
import json, subprocess, sys, tempfile
from pathlib import Path
import numpy as np, soundfile as sf, librosa

ROOT = Path(__file__).resolve().parent.parent
SR = 48000
G = json.loads((ROOT / "sound/grid.json").read_text())
DROP, END_CARD, DUR = G["drop"], G["end_card"], G["duration"]

# each line's start in film seconds (the music's drop, where the groove enters, is 12.665 s; see sound/music.py)
SLOTS = {"v01": 0.5, "v02": 2.6, "v03": 5.6, "v04": 8.4, "v05": 12.9, "v06": 17.25, "v07": 24.0, "v08": 28.5,
         "v09": 32.5, "v10": 40.5, "v11": 44.5, "v12": 48.25, "v13": 50.5, "v14": 57.0}
# scene windows (film seconds)
SCENES = {"s1": [0, 16.75], "s2": [16.75, 23.5], "s3": [23.5, 32.0], "s4": [32.0, 40.0], "s5": [40.0, 50.0],
          "s6": [50.0, 56.5], "s7": [END_CARD, DUR]}   # s7 starts on the outro; s6 runs on under the night rising


def lufs(path):
    out = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    summ = out[out.rfind("Summary:"):]
    i = float(summ.split("I:")[1].split("LUFS")[0])
    tp = float(summ.split("Peak:")[1].split("dBFS")[0]) if "Peak:" in summ else 0.0
    return i, tp


def to_stereo(y):
    return np.vstack([y, y]) if y.ndim == 1 else y


def load(path, sr=SR):
    y, s = sf.read(path, always_2d=True)
    y = y.T.astype(np.float64)
    if s != sr:
        y = np.vstack([librosa.resample(ch, orig_sr=s, target_sr=sr) for ch in y])
    return y


def main():
    words = json.loads((ROOT / "narration/words.json").read_text())
    music = load(ROOT / "assets/audio/music.wav")
    n = music.shape[1]

    # ---------------------------------------------------------------- the voice
    vo = np.zeros(n)
    meta, prev_end = {}, 0.0
    for key, t0 in SLOTS.items():
        y = load(ROOT / f"narration/chosen/{key}.wav").mean(0)
        if t0 < prev_end + 0.25:
            print(f"warning: {key} starts {prev_end + 0.25 - t0:.2f} s too early", file=sys.stderr)
        a = int(round(t0 * SR)); b = min(n, a + len(y))
        vo[a:b] += y[: b - a]
        end = t0 + len(y) / SR
        meta[key] = {"start": round(t0, 3), "end": round(end, 3), "text": words[key]["text"],
                     "words": [{"w": w["w"], "t0": round(t0 + w["t0"], 3), "t1": round(t0 + w["t1"], 3)} for w in words[key]["words"]]}
        prev_end = end
    tmp = Path(tempfile.mkdtemp())
    sf.write(tmp / "vo_raw.wav", vo.astype(np.float32), SR)
    # Kokoro is clean and bright already: a high-pass, a touch of presence, light de-essing and compression
    chain = ("highpass=f=80,equalizer=f=3000:t=q:w=1.2:g=1.5,equalizer=f=250:t=q:w=1.0:g=-1,"
             "deesser=i=0.2:f=0.5,acompressor=threshold=0.1:ratio=2.5:attack=10:release=150:makeup=1.3")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp / "vo_raw.wav"), "-af", chain, "-ar", str(SR),
                    str(ROOT / "assets/audio/vo.wav")], check=True)
    vo = load(ROOT / "assets/audio/vo.wav").mean(0)[:n]
    vo = np.pad(vo, (0, n - len(vo)))

    # ---------------------------------------------------------------- the bed: quieter, less boom, ducked by the voice
    sf.write(tmp / "m.wav", music.T.astype(np.float32), SR)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp / "m.wav"), "-af",
                    "bass=g=-4:f=110:w=0.7,equalizer=f=2500:t=q:w=1.5:g=-2", str(tmp / "m2.wav")], check=True)
    bed = load(tmp / "m2.wav")
    sf.write(tmp / "v.wav", vo.astype(np.float32), SR)
    vi, _ = lufs(tmp / "v.wav")
    bi, _ = lufs(tmp / "m2.wav")
    # speech at -16 LUFS (measured on the voice alone, so silences count: correct for the 58% of the film she speaks)
    speech_frac = sum(m["end"] - m["start"] for m in meta.values()) / DUR
    vi_speech = vi - 10 * np.log10(speech_frac)
    vo *= 10 ** ((-16 - vi_speech) / 20)
    bed *= 10 ** ((-25 - bi) / 20)            # the bed 9 dB under the speech before ducking
    # duck: -4.5 dB under the voice, 40 ms attack, 450 ms release (followed at 1 kHz, then interpolated)
    hop = SR // 1000
    env = np.sqrt(np.convolve(vo ** 2, np.ones(int(0.05 * SR)) / int(0.05 * SR), "same"))[::hop]
    on = (env > 10 ** (-45 / 20)).astype(float)
    g = np.empty(len(on)); state = 0.0
    att, rel = 1 - np.exp(-1 / (0.04 * 1000)), 1 - np.exp(-1 / (0.45 * 1000))
    for i, target in enumerate(on):
        state += (att if target > state else rel) * (target - state)
        g[i] = state
    g = np.interp(np.arange(n), np.arange(len(g)) * hop, g)
    duck = 10 ** (-4.5 * g / 20)
    bed *= duck

    # ---------------------------------------------------------------- effects
    sfx = np.zeros((2, n))
    TMd = {"drop": DROP, **SCENES}
    W = word_finder(meta)
    for t, name, gain_db, pitch in sfx_events(W, TMd, meta):
        place(sfx, name, t, gain_db, pitch, bed_rms_db=-25 - 4.5)

    # ---------------------------------------------------------------- sum, level, limit
    mix = bed + sfx + np.vstack([vo, vo])
    sf.write(tmp / "mix.wav", mix.T.astype(np.float32), SR)
    mi, _ = lufs(tmp / "mix.wav")
    mix *= 10 ** ((-16 - mi) / 20)
    sf.write(tmp / "mix2.wav", mix.T.astype(np.float32), SR)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp / "mix2.wav"), "-af", "alimiter=limit=0.82:attack=3:release=60:level=disabled",
                    "-ar", str(SR), str(ROOT / "assets/audio/mix.wav")], check=True)
    fi, tp = lufs(ROOT / "assets/audio/mix.wav")

    (ROOT / "assets/vo.js").write_text("window.VO = " + json.dumps(meta) + ";\n")
    tm = {"drop": DROP, "end_card": END_CARD, "track_ending": G["track_ending"], "duration": DUR, **SCENES}
    (ROOT / "assets/timing.js").write_text("window.TM = " + json.dumps(tm) + ";\n")
    (ROOT / "sound/timing.json").write_text(json.dumps(tm, indent=1))
    print(f"mix.wav {DUR} s, {fi:.1f} LUFS, true peak {tp:.1f} dBTP; voice, bed and {len(sfx_events(W, TMd, meta))} effects")
    print("lines: " + ", ".join(f"{k} {m['start']:.2f}-{m['end']:.2f}" for k, m in meta.items()))


def word_finder(meta):
    norm = lambda s: "".join(c for c in s.lower() if c.isalnum())
    def W(k, w, nth=0):
        alts = w.split("|")
        ws = meta[k]["words"]
        for exact in (True, False):
            c = 0
            for x in ws:
                t = norm(x["w"])
                if any((t == a) if exact else t.startswith(a) for a in alts):
                    if c == nth: return x["t0"]
                    c += 1
        print(f"warning: no word {w!r} in {k}", file=sys.stderr)
        return meta[k]["start"]
    return W


SFX = {  # file, the RMS level it is normalised to before its event gain
    "whoosh_wind": "mixkit-1465.mp3", "whoosh_air": "mixkit-1492.mp3", "sweep": "mixkit-3115.mp3",
    "tick": "mixkit-1120.mp3", "pop": "mixkit-2357.mp3", "pop_hard": "mixkit-3005.mp3",
    "typing": "mixkit-2538.mp3", "riser": "mixkit-2408.mp3",
}
_cache = {}


def clip(name):
    if name not in _cache:
        y = load(ROOT / "sound/sfx-src" / SFX[name])
        if name == "typing":   # a short burst of keys from the middle of the take
            y = y[:, int(1.0 * SR): int(1.75 * SR)]
            f = int(0.03 * SR); y[:, :f] *= np.linspace(0, 1, f); y[:, -f:] *= np.linspace(1, 0, f)
        if name == "riser":    # let it swell in from nothing
            f = int(1.2 * SR); y[:, :f] *= np.linspace(0, 1, f) ** 2
        peak = np.argmax(np.convolve(np.abs(y).mean(0), np.ones(240) / 240, "same")) / SR
        rms = 20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9)
        _cache[name] = (y, peak, rms)
    return _cache[name]


def place(out, name, t, gain_db, pitch, bed_rms_db):
    y, peak, rms = clip(name)
    if pitch != 1.0:   # small pitch steps rotate repeated sounds (resampling also shortens or lengthens them)
        y = np.vstack([librosa.resample(ch, orig_sr=SR, target_sr=int(SR / pitch)) for ch in y]); peak /= pitch
    # level relative to the bed's RMS, so "+4" means about 4 dB over the music under it
    y = y * 10 ** ((bed_rms_db + gain_db - rms) / 20)
    a = int(round((t + 1 / 30 - peak) * SR))   # its peak one frame after the picture
    s0 = max(0, -a); a = max(0, a)
    b = min(out.shape[1], a + y.shape[1] - s0)
    out[:, a:b] += y[:, s0: s0 + (b - a)]


def sfx_events(W, TM, meta):
    """(film time of the picture, sound, dB over the bed, pitch). Mirrors the scenes' own timings (compositions/*.html)."""
    e = []
    # s1: the title folds into the ledger's header; two failed attempts land as rows; "said." is struck; the drop
    e.append((W("v02", "one") - 0.15, "whoosh_air", -2, 1.0))
    e.append((W("v02", "learns"), "tick", 0, 1.0))
    e.append((W("v02", "deadlocks") + 0.1, "pop", -1, 1.0))
    e.append((W("v03", "tuesday"), "tick", 0, 1.06))
    e.append((W("v03", "again") + 0.1, "pop", -1, 1.06))
    e.append((meta["v04"]["end"] + 0.25, "tick", -1, 0.9))
    e.append((TM["drop"], "whoosh_wind", 2, 1.0))
    # s2: the three kinds of proof land as numbered rows
    e.append((TM["s2"][0] + 0.1, "sweep", -1, 1.0))
    for k, w in enumerate(("command", "reviewer", "commit")):
        e.append((W("v06", w), "pop", 1, (1.0, 1.05, 1.1)[k]))
    # s3: the ladder's outlines draw; five rungs fill bottom to top, each a step higher; the light at the top
    e.append((TM["s3"][0] + 0.25, "whoosh_air", -1, 1.06))
    c = W("v07", "climbs")
    for k in range(5):
        e.append((c + 0.28 * k, "tick", -1, (1.0, 1.06, 1.12, 1.19, 1.26)[k]))
    e.append((W("v07", "skipped") + 0.1, "pop", 2, 1.12))
    e.append((W("v08", "never"), "pop", 0, 0.94))
    # s4: the sun panel wipes in; the preflight rows print; the closing line lands
    e.append((TM["s4"][0] + 0.05, "whoosh_wind", 1, 1.06))
    rows = preflight_rows(W)
    e.append((rows[0] - 0.05, "typing", -4, 1.0))
    for k, t in enumerate(rows[1:]):
        e.append((t, "tick", -3, (1.0, 1.04, 0.97, 1.08, 1.0, 1.1)[k % 6]))
    e.append((meta["v09"]["end"] + 0.3, "pop", 0, 1.0))
    # s5: no LLM / no API key / no network; 777 counts up and lands; open source
    for k, w in enumerate(("llm", "api", "network")):
        e.append((W("v10", w), "pop", 0, (1.0, 1.05, 1.1)[k]))
    e.append((W("v10", "api") + 0.35, "tick", -2, 1.0))
    e.append((W("v11", "tests") - 0.05, "pop_hard", -1, 1.0))
    e.append((W("v12", "open"), "pop", -1, 1.04))
    # s6: the commands type; the demo's lines print
    for t in term_times(TM, meta):
        e.append(t)
    # s7: night rises with one riser into the light; the wordmark lands on the voice; the command line
    e.append((TM["s7"][0] + 1.0, "riser", 0, 1.0))
    e.append((W("v14", "provo|prova"), "pop", 2, 0.9))
    e.append((TM["s7"][0] + 2.3, "tick", -1, 1.0))
    return e


def preflight_rows(W):
    """When each preflight row prints (s4 uses the same rule): the command on 'before', then the rows across the line."""
    t0 = W("v09", "before")
    return [t0] + [t0 + 0.42 * (k + 1) for k in range(6)]


def term_times(TM, meta):
    """The s6 terminal: two commands type, then four output lines print (s6 uses the same numbers)."""
    s = TM["s6"][0]
    out = [(s + 1.3, "typing", -4, 1.0), (s + 2.55, "typing", -4, 1.06)]
    for k in range(4):
        out.append((s + 3.2 + 0.32 * k, "tick", -3, (1.0, 1.05, 0.97, 1.1)[k]))
    return out


if __name__ == "__main__":
    main()
