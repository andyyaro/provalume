# Third-party sounds and type in the film

These are in the film but not in this repository. Their licences cover using them inside a film,
not handing the files on by themselves, so they are listed here instead. Download each one again
to the path shown to rebuild.

## Music

| Used as | Track | Licence | Put it at |
|---|---|---|---|
| The bed under the whole film, cut on its bar lines by `sound/music.py` | "Baileys" by Diego Nava, [Mixkit 476](https://assets.mixkit.co/music/476/476.mp3) (contemporary R&B, 95 BPM, 1:37) | [Mixkit Stock Music Free License](https://mixkit.co/license/): free in any project, no credit required, not for use in a music-only track | `sound/track.mp3` |

## Sound effects

All from Mixkit, under the [Mixkit Sound Effects Free License](https://mixkit.co/license/).
The names are the ones `sound/mix.py` uses (`SFX`); each file goes in `sound/sfx-src/` as
`mixkit-<id>.mp3`.

| Name in `mix.py` | Mixkit id | What it marks |
|---|---|---|
| `pop` | 2357 | A row or a word landing: the two failed attempts, the three kinds of proof, "skipped", "never", "No LLM. No API key. No network.", "Open source" and the wordmark on the end card |
| `tick` | 1120 | A step: the ledger's rows, "said." struck out, each of the five rungs filling, the preflight rows and the terminal's lines |
| `whoosh_air` | 1492 | The cover title folding into the ledger; the ladder's outlines drawing |
| `whoosh_wind` | 1465 | The drop on "Provalume keeps what was proved."; the sun panel wiping in |
| `sweep` | 3115 | The move into the three kinds of proof |
| `pop_hard` | 3005 | 777 landing |
| `typing` | 2538 | The commands typing: the preflight command and the two in the terminal (a short burst from the middle of the take) |
| `riser` | 2408 | Night rising on the end card, into the mark's light |

## Voice

Kokoro-82M ([Apache-2.0](https://huggingface.co/hexgrad/Kokoro-82M)), voice af_heart, run
locally. The lines are `narration/lines-tts.json`; "Provalume" is voiced PRO-vuh-loom
(`[Provalume](/pɹˈOvəlum/)`). The audio is generated, so it is not kept.

## Type

Instrument Serif (Regular and Italic), Archivo (variable) and Montserrat SemiBold, all under the
[SIL Open Font License 1.1](https://openfontlicense.org/), from
[Google Fonts](https://fonts.google.com/). They go in `assets/fonts/` as `.woff2` files with the
names `index.html` loads: `InstrumentSerif-400.woff2`, `InstrumentSerif-400-italic.woff2`,
`Archivo-var.woff2` and `Montserrat-SemiBold.woff2`.

## Pictures

There are none: the film has no screen recordings, photographs or 3D models. Every frame is drawn
by the composition, and the product's output on screen is set in type from the real output (see
[STORYBOARD.md](../../STORYBOARD.md)).
