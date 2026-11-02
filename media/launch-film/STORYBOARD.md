# Provalume launch film: storyboard and build

62.5 s, 1920 × 1080, 30 fps (1,875 frames), made with HyperFrames 0.8.173 against Provalume 0.1.4.
H.264 with AAC, 48 kHz stereo; measured on the MP4: -16.1 LUFS integrated, true peak -1.5 dBTP,
loudness range 8.2 LU. This is v2: the same narration as v1, with new music ("Baileys" by Diego
Nava in place of "Peace", the Orkestra film's track). The brief, with every number of the look, is
[SPEC.md](SPEC.md).

## The film

| Time (s) | Scene | Narration | What you see |
|---|---|---|---|
| 0–16.75 | 01 Open | "Your coding agents forget. One learns that the integration suite deadlocks. On Tuesday, the next one tries it again. Most agent memory keeps what an agent said." On the drop (12.67 s): "Provalume keeps what was proved." | The cover, "Your agents forget.", folds into the header of a ledger of attempts on the integration suite: agent-A ran `pytest -n auto tests/integration` and got "TimeoutError: deadlock in db fixture teardown", exit 1; agent-B tried the same suite the same way. "Most memory keeps what agents said.", then "said." is struck out and the line becomes "Provalume keeps what was proved." as the sun swells behind it. |
| 16.75–23.5 | 02 Proof | "A command that passed. A reviewer who was not the author. A commit that actually landed." | "What counts as proof", three numbered rows, each with the rung it earns and its line from `provalume demo`: "exit 0 · 43 passed · pytest -p no:xdist tests/integration" (verified), "reviewer-2 is not the author (agent-B), so the review counts as independent" (reviewed), "landed at 8b746c0fd360" (integrated). |
| 23.5–32 | 03 Ladder | "Every memory climbs five rungs of trust, and none can be skipped. Agents propose. They never promote." | The trust ladder drawn as the Provalume mark: five rungs fill from ember to cream as the voice climbs, and the light at the top comes on. Each rung has its name and meaning from the README (quarantined: agent prose; observed: reported from a run; verified: a command returned; reviewed: a non-author approved it; integrated: it landed in history) and, on the top three, the rule that promotes to it (`promote.procedural.verified_by_exact_command`, `promote.any.independent_review_approved`, `promote.any.landed_in_history`). |
| 32–40 | 04 Warn | "So before an agent repeats a failure, it is warned, with the evidence." | The sun panel wipes in: "Warned before it repeats." On the paper side, `provalume preflight --command "pytest -n auto tests/integration"` prints its warning row by row, then "This is a warning, not a block. Provalume does not override policy." |
| 40–50 | 05 Facts | "No LLM. No API key. No network. Seven hundred and seventy-seven tests, all passing. Open source, on PyPI." | "No LLM. No API key. No network." over a footer of facts (dependencies: pydantic, typer, rich; storage: one SQLite file; telemetry: none, and no account; licence: Apache-2.0). 777 counts up as the jumbo numeral, "tests, all passing.", "pytest on provalume 0.1.4 · 354 of them security tests". "Open source, on PyPI.", pypi.org/project/provalume, Apache-2.0 · v0.1.4. |
| 50–56.5 | 06 Try | "Try it in sixty seconds." | "60-second quickstart" over a hairline terminal: `uv tool install provalume`, `provalume demo`, and four of the demo's lines ("4. Agent B is warned before repeating it", "7. An independent reviewer approves", "9. The procedure is promoted to verified+landed", "Audit · 11 checks passed."). |
| 55.6–62.5 | 07 End card | "Provalume." | Night rises to the brand's indigo; the mark builds rung by rung and its light comes on; the wordmark; "Facts your agents proved, not things they said." in amber; `uv tool install provalume`; github.com/andyyaro/provalume. |

## What is real and what is rebuilt

- **Real:** every line of product output and every figure. The ledger, the proof rows and the
  terminal are the output of `provalume demo` (`src/provalume/demo/scenario.py`). The preflight
  block is the README's own example. The rungs and their meanings are the README's trust ladder;
  the three rule names are the ones the demo prints (`src/provalume/policy/promotion.py`).
  777 tests passing, 354 of them security tests, is pytest on 0.1.4 (main at 194d251,
  2026-11-01). The facts footer is `pyproject.toml` (three dependencies), the README ("One
  SQLite file", "no telemetry ... and no account") and `LICENSE`.
- **Rebuilt:** nothing on screen is a screen recording. The output is set in Archivo Medium
  instead of a terminal's monospace font (these films use no monospace on screen), and the demo's
  own em dashes are not shown. The preflight block leaves out the README's opening banner and two
  of its eight rows (applicability and provenance), and shows "What later worked" without its
  timestamp. The story of the two agents is the README's opening example, drawn as a ledger.

## Look

- **Style:** the HyperFrames frame preset "Biennale Yellow", an art-biennale catalogue of paper,
  ink and one sun bloom, moved onto Provalume's own colours. Frames read 55-60% empty; landed type
  holds still; the sun moves between frames and the paper never cuts.
- **Colours:** paper #EEE8DA, ink #15123B (Provalume's indigo, every line of type and every rule),
  sun #FFB547 fading through #FFD37E and #FFEBB9 to the paper, ember #F2602F only as a faint
  counter-bloom and the bottom rung of the mark. The end card leaves the preset on purpose: night
  falls to the brand ground #15123B and the mark is lit as a lamp ("Provalume": prova + lume,
  proof + light).
- **Type:** Instrument Serif for every display line, numeral and page number (italic for the one
  key word), Archivo for body text and Archivo Medium for commands, hashes and figures, Montserrat
  SemiBold capitals for the small labels. No monospace and no em dashes on screen.
- **Rules and depth:** 1 px ink hairlines are the only borders; no shadows and no rounded cards
  (the mark's rounded rungs are the one exception, because they are the brand); a 4% paper grain.
- **Transitions:** the cover title folds into the ledger header; blur swaps on one stage; the sun
  panel wipes across from the left; night rises from the bottom on the end card.

## Sound

- **Voice:** Kokoro-82M, voice af_heart, 14 lines. Each line is placed at a fixed time
  (`SLOTS` in `sound/mix.py`) and the scenes find their cue words by text in `window.VO`, so a
  re-voiced line re-times the picture. "Provalume" is voiced PRO-vuh-loom
  (`[Provalume](/pɹˈOvəlum/)` in `narration/lines-tts.json`), PyPI as "Pie Pee Eye" and 777 as
  "seven hundred and seventy-seven".
- **Music:** "Baileys" by Diego Nava (Mixkit 476), smooth contemporary R&B at 95 BPM (a bar is
  2.526 s), calm but with a groove. See the edit below.
- **Effects:** eight soft Mixkit sounds, each placed so its peak lands one frame after its
  picture, 3-6 dB over the bed. The list is in [assets/audio/SOURCE.md](assets/audio/SOURCE.md).
- **Levels:** speech at -16 LUFS, the bed 9 dB under it and ducked 4.5 dB more while she speaks;
  the mix at -16 LUFS with the true peak under -1.5 dBTP.

## The music edit

`sound/music.py` cuts the track on its bar lines, each join with a 12 ms crossfade:

| Film (s) | Track (s) | What it is |
|---|---|---|
| 0.000–5.087 | 0.000–5.087 | The pickup and the first intro bar |
| 5.087–7.613 | 2.561–5.087 | The first intro bar again, so the groove lands on the film's drop |
| 7.613–12.665 | 5.087–10.140 | Intro bars 1-2 |
| 12.665–42.981 | 10.140–40.456 | The groove enters on the drop, under "Provalume keeps what was proved." |
| 42.981–55.613 | 60.666–73.298 | The breakdown is skipped: the second groove, bars 23-27 |
| 55.613–62.500 | 90.982–end | The track's own outro under the end card, decaying out |

## Build

Get the inputs first: `tools/fetch_vendor.sh` for GSAP, then the fonts, music and effects listed
in [assets/audio/SOURCE.md](assets/audio/SOURCE.md). The Python steps need numpy, soundfile,
librosa and mlx-whisper (`<voice-venv>`), and ffmpeg.

```sh
tools/fetch_vendor.sh
# Voice each line of narration/lines-tts.json with Kokoro-82M (af_heart) at speeds 1.0 and 1.06,
# as narration/takes/<id>.<k>.wav
<voice-venv>/bin/python -I narration/pick.py takes      # best take per line, words.json
<voice-venv>/bin/python -I sound/music.py               # assets/audio/music.wav, sound/grid.json
<voice-venv>/bin/python -I sound/mix.py                 # mix.wav, assets/vo.js, assets/timing.js
python3 -I build.py                                     # scene windows into index.html
python3 -I tools/captions.py                            # renders/provalume-v2.en.vtt
npx --yes hyperframes@0.8.173 lint && npx --yes hyperframes@0.8.173 check
npx --yes hyperframes@0.8.173 render -o renders/provalume-v2.mp4 --fps 30
```

The soundtrack (`assets/audio/mix.wav`) is a clip in `index.html`, so the render carries it. The
times to check the picture at are listed under QA in [SPEC.md](SPEC.md).

## The README preview

`docs/assets/readme/provalume-in-action.webp` is 23.6–39.6 s of the film: the trust ladder
filling rung by rung, then the preflight warning printing row by row. 1200 × 676, 15 fps, lossy
quality 90, looping, 1.3 MB.

```sh
mkdir -p preview
ffmpeg -ss 23.6 -t 16 -i renders/provalume-v2.mp4 \
  -vf "fps=15,scale=1200:676:flags=lanczos" preview/f%03d.png
img2webp -loop 0 -lossy -q 90 -m 6 -d 67 preview/f*.png \
  -o ../../docs/assets/readme/provalume-in-action.webp
```
