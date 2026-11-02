# Provalume launch film: SPEC

```text
GOAL:    a developer who runs coding agents understands that Provalume remembers only what was proved,
         sees the trust ladder and the preflight warning, and types `uv tool install provalume`.
OUTPUT:  HyperFrames 0.8.173, 1920 x 1080, 30 fps, 62.5 s = 1875 frames.
SOUND:   "Baileys" (Diego Nava, Mixkit 476), smooth R&B, 95 BPM (bar 2.526 s). Soft intro to 12.66 s, the
         groove enters on the drop ("Provalume keeps what was proved."), the breakdown is cut out, and the
         track's own outro runs under the end card from 55.61 s (see sound/music.py). Voice Kokoro af_heart, speech -16 LUFS, the bed 9 dB under it
         and ducked 4.5 dB more while she speaks. Effects few and soft, 3-6 dB over the bed, peak 1 frame
         after the picture. Mix -16 LUFS, true peak under -1.5 dBTP.
LOOK:    The reference is the HyperFrames frame preset "Biennale Yellow"
         (skills/hyperframes/assets/frame-presets/biennale-yellow: FRAME.md + frame-showcase.html): a
         literary-editorial system in the register of an art-biennale catalogue. Paper, ink, and sun. Its
         atoms are kept; its colours are moved onto Provalume's own brand, which already speaks it
         (Instrument Serif wordmark, deep indigo, amber):
           paper    #EEE8DA (ground on every frame), paper-deep #E2DAC6
           ink      #15123B (Provalume indigo): every line of type and every rule. No second text colour.
           sun      #FFB547 core -> #FFD37E -> #FFEBB9 -> paper: the bloom, the panel, the ladder's fill
           ember    #F2602F: only a 16-20% counter-bloom, and the bottom rung of the mark
           type     Instrument Serif 400 for every display, numeral, quote and the pagenum (tight, -0.018em;
                    italic for the one key word); Archivo for body, and Archivo Medium for commands, hashes and
                    figures; Montserrat SemiBold caps (+0.14em, 16-20 px) for the micro-labels (the approved
                    label face). NO MONOSPACE anywhere: the preset's JetBrains Mono "data" face is replaced
                    (these films use no monospace).
                    Video scale: display 250 px, headline 150 px, headline-sm 96-110 px, numeral 520 px,
                    body 26-30 px, commands 23-32 px.
           rules    1 px ink hairlines are the only border (soft rows: ink at 18%). Zero shadow, zero radius
                    (the mark's own rounded rungs are the one exception: they are the brand).
           depth    one sun bloom per frame (+ one ember counter-bloom), a 4% paper grain. Frames read
                    55-60% empty; the ledger and the preflight block are the dense exceptions.
           chrome   pagenum "0N / 07" bottom-right, Instrument Serif 30 px, ink 75%.
         The end card leaves the preset on purpose: night falls to the brand ground #15123B and the mark is
         lit as a lamp (the brand's lockup is made for that ground). "Provalume": prova + lume, proof + light.
OBJECTS: real product only: the README's own story (an agent learns the integration suite deadlocks
         under -n auto; the next tries it again on Tuesday); the real `provalume demo` output (agent-A,
         agent-B, "TimeoutError: deadlock in db fixture teardown", exit 0 · 43 passed, reviewer-2 is not the
         author, landed at 8b746c0fd360, the three promotion rule names, Audit 11 checks passed); the real
         `provalume preflight` output from the README; the five rungs and their meanings from the README;
         777 tests passing and 354 security tests (pytest on 0.1.4, main @194d251, 2026-11-01); three
         dependencies (pydantic, typer, rich); one SQLite file; no telemetry; Apache-2.0; PyPI 0.1.4.
MOTION:  restrained. Serif words rise 24 px with blur 6 -> 0 and opacity, 0.5 s power3.out, on the voice's
         own word times (window.VO). Rows and rules: hairlines draw by scaleX 0.5 s power2.inOut. Exits
         power3.in at about half the entrance. The sun bloom travels between frames (power2.inOut 1.2 s):
         the light moves, the paper never cuts. Landed type holds dead still.
TRANSITIONS (4 kinds): the cover title folds into the ledger header (scale, transform-origin 0 0);
         blur swaps on one stage; the sun panel wipes across (scaleX from the left edge); night rises
         from the bottom on the end card.
SHOTS:   s1 open     0-16.75  cover "Your agents forget." -> the ledger of two failed attempts -> "Most memory
                              keeps what agents said." -> "said." struck on the build -> on the drop it
                              becomes "Provalume keeps what was proved." and the sun swells behind it.
         s2 proof    16.75-23.5 strand list: 01 a command that passed, 02 a reviewer who was not the author,
                              03 a commit that actually landed; each with its line from the demo.
         s3 ladder   23.5-32   the trust ladder drawn as the mark itself: five hairline rungs fill from ember
                              to cream as the voice climbs, the light at the top; the real rule names;
                              "Agents propose. They never promote."
         s4 warn     32-40     poster panel: "Warned before it repeats." in ink on the sun panel; the real
                              preflight output prints on the paper side; "This is a warning, not a block."
         s5 facts    40-50     "No LLM. No API key. No network." + a footer band of facts; then 777 as the
                              jumbo numeral, "tests, all passing"; "Open source, on PyPI."
         s6 try      50-56     "Try it in sixty seconds." + a hairline terminal: install, demo, four real lines.
         s7 end      56-62.5   night rises; the mark builds rung by rung and its light comes on; the
                              wordmark; the tagline in amber; `uv tool install provalume`; the repo.
BANNED:  monospace fonts anywhere on screen; em dashes anywhere on screen (the films' rules; the demo's own
         em dashes are not shown); the Huly look (cream editorial with product cards); the Linear look of Orkestra v2 (dark grid,
         product windows); shadows, rounded cards, borders over 1 px, a second text colour, yellow text on
         ink inside the preset frames, glows anywhere but the end card's lamp, floating loops, everything
         centred, the logo docked in a corner, three feature cards.
QA:      1.9 (cover title), 6.9 (two ledger rows), 11.4 (said. struck), 13.6 (proved. on the sun),
         21.8 (three rows), 27.4 (ladder full + light), 30.4 (propose/promote), 36.9 (preflight block),
         43.3 (no network + footer), 47.0 (777), 49.6 (PyPI), 54.8 (terminal), 60.5 (end card).
```
