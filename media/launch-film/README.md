# Provalume launch film

The 63-second film at the top of the [repository README](../../README.md), published at
https://andyyaro.github.io/provalume/film.html. It tells the README's own story: one agent learns
that the integration suite deadlocks, the next tries it again on Tuesday, and Provalume keeps only
what was proved. Then the three kinds of proof, the trust ladder, the preflight warning, what it
needs (no LLM, no API key, no network), its 777 passing tests and how to try it. Every line of
output and every figure on screen comes from the product: `provalume demo`, the README's preflight
example and trust ladder, and pytest on 0.1.4.

[STORYBOARD.md](STORYBOARD.md) has the scenes, the look, the sound and every build step.
[SPEC.md](SPEC.md) is the brief the film was made to, with every colour, size and timing in it.

## What is here

| Path | What it is |
|---|---|
| `index.html`, `compositions/` | The composition (HyperFrames 0.8.173, GSAP): one file per scene, seven scenes |
| `build.py` | Writes the scene windows from `sound/timing.json` into `index.html` |
| `sound/` | The music edit (`music.py`) and the mix (`mix.py`): narration, music and effects, placed on the voice's own word times |
| `narration/` | The script (`lines.json`), the script as voiced (`lines-tts.json`) and `pick.py`, which chooses the best take of each line and times its words |
| `SPEC.md` | The brief: goal, sound, look, the real objects on screen, motion, shots, what is banned, and the times to check |
| `tools/captions.py` | Writes the captions file from the narration's word times |
| `tools/fetch_vendor.sh` | Fetches GSAP at the pinned version and checks the file's SHA-256 |
| `deploy/film.html`, `deploy/index.html` | The watch page, and a redirect to it from the site's root |
| `assets/brand/wordmark-cream.svg` | The wordmark on the end card. The mark is drawn in `compositions/s7-end.html` from the repository's [`mark-color.svg`](../../docs/assets/brand/mark/mark-color.svg) |
| `assets/audio/SOURCE.md` | Every third-party sound and font in the film, with its licence and where it goes |

## What is not here, and why

The working folder for the film is about 82 MB. This folder keeps the source and leaves out
everything that is generated or that its licence does not let us hand on by itself:

- **Generated:** the narration takes, the chosen lines and their word times, the music edit, the
  mix and the voice track, the timing files (`assets/vo.js`, `assets/timing.js`,
  `sound/grid.json`, `sound/timing.json`), the renders and the stills used to check them. The
  build steps make them again.
- **Third-party media:** the music ("Baileys" by Diego Nava, Mixkit) and eight Mixkit sound
  effects. [assets/audio/SOURCE.md](assets/audio/SOURCE.md) lists each one with its licence and
  the path it goes to.
- **Libraries and fonts:** GSAP 3.14.2 is fetched by `tools/fetch_vendor.sh`. The Instrument
  Serif, Archivo and Montserrat fonts (SIL OFL 1.1) go in `assets/fonts/`.
- **The logo files:** the film's working folder had copies of the lockup and the mark; they are
  the repository's own, in [`docs/assets/brand/`](../../docs/assets/brand/).

There are no screen recordings, photographs or 3D models to leave out: every frame is drawn by the
composition.

## Publishing

The MP4, its captions and its poster are assets of the
[`film-v1` release](https://github.com/andyyaro/provalume/releases/tag/film-v1), so the 19.9 MB
film is not in the repository's history. The
[film page workflow](../../.github/workflows/film.yml) puts them on GitHub Pages with
`deploy/film.html`, `deploy/index.html` and the logo: the mark, the wordmark and the tab icon. It
runs when anything in `deploy/` changes on `main`. After replacing a release asset, run it by hand:

```sh
gh release upload film-v1 provalume-launch.mp4 --clobber
gh workflow run film.yml
```

## Credits

Narration: Kokoro-82M (Apache-2.0), voice af_heart. Music: "Baileys" by Diego Nava (Mixkit).
Sound effects: Mixkit. Type: Instrument Serif, Archivo and Montserrat (SIL OFL 1.1). The look
starts from the HyperFrames frame preset "Biennale Yellow", moved onto Provalume's own colours.
