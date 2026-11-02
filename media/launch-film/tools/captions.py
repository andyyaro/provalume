"""Write the film's captions (WebVTT) from the narration's own word times.

    python3 -I tools/captions.py      # assets/vo.js (sound/mix.py) -> renders/provalume-v2.en.vtt

The film has no subtitles in the picture, so these captions are the only text track: the watch page plays them and
the film-v1 release carries them as provalume-launch.vtt. The cues follow the nearsay film's rules: at most 2 lines of
42 characters, each cue starts 0.08 s before its first word and ends 0.30 s after its last (0.04 s before the next).
The text is the narration as voiced (narration/lines-tts.json: "wasn't", "it's"), with PyPI and 777 written as they
are on screen. Each cue must be the next words of its line, counted against the words in window.VO.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEAD, TAIL, GAP = 0.08, 0.30, 0.04
CUES = [
    ("v01", "Your coding agents forget."),
    ("v02", "One learns that the integration\nsuite deadlocks."),
    ("v03", "On Tuesday, the next one tries it again."),
    ("v04", "Most agent memory keeps\nwhat an agent said."),
    ("v05", "Provalume keeps what was proved."),
    ("v06", "A command that passed."),
    ("v06", "A reviewer who wasn't the author."),
    ("v06", "A commit that actually landed."),
    ("v07", "Every memory climbs five rungs of trust,\nand none can be skipped."),
    ("v08", "Agents propose. They never promote."),
    ("v09", "So before an agent repeats a failure,\nit's warned, with the evidence."),
    ("v10", "No LLM. No API key. No network."),
    ("v11", "777 tests, all passing."),
    ("v12", "Open source, on PyPI."),
    ("v13", "Try it in sixty seconds."),
    ("v14", "Provalume."),
]
# how the speech recogniser spelled a few words (narration/pick.py keeps what it heard)
HEARD = {"provolume": "provalume", "60": "sixty"}


def norm(s):
    return re.sub(r"[^a-z0-9']", "", s.lower())


def ts(x):
    ms = round(max(0.0, x) * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def main():
    vo_js = (ROOT / "assets/vo.js").read_text()
    vo = json.loads(vo_js[vo_js.index("{") : vo_js.rindex("}") + 1])
    pos, out = {}, []
    for k, text in CUES:
        n = len(text.split())
        words = vo[k]["words"][pos.get(k, 0) : pos.get(k, 0) + n]
        heard = [HEARD.get(norm(w["w"]), norm(w["w"])) for w in words]
        assert heard == [norm(w) for w in text.split()], f"{k}: {text!r} is not what was heard: {heard}"
        for line in text.split("\n"):
            assert len(line) <= 42, f"{k}: caption line over 42 characters: {line!r}"
        pos[k] = pos.get(k, 0) + n
        out.append([words[0]["t0"] - LEAD, words[-1]["t1"] + TAIL, text])
    for k in vo:
        assert pos.get(k) == len(vo[k]["words"]), f"{k}: not every word is captioned"
    for a, b in zip(out, out[1:], strict=False):
        a[1] = min(a[1], b[0] - GAP)
    dest = ROOT / "renders/provalume-v2.en.vtt"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(
        "WEBVTT\n\n" + "".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(out, 1))
    )
    print(f"{dest.relative_to(ROOT)}: {len(out)} cues")


if __name__ == "__main__":
    main()
