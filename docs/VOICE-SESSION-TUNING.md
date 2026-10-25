# Voice session tuning

How well the voice session (`karaoke_decide/voice_session/`) finds a singer's voice type within 3–5 songs,
measured two ways:

1. **Simulated singers.** `scripts/voice/sim_report.py` runs prior × pitch × k.
2. **Replays of real sessions.** `karaoke_decide.voice_session.replay` replays fixtures exported with
   `scripts/voice/export_session_fixture.py`.

Plan: nomadkaraoke workspace `docs/archive/2026-10-25-mobile-phase3d-tuning-batch-1-plan.md` (Part E).
This page records the **baseline before any tuning (Task E1, v0.31.x weights)**. E2–E5 add their
before/after numbers below it.

## How to run

```bash
poetry run python scripts/voice/sim_report.py              # seed 20261025, n 300 per row, ~35 s
poetry run python scripts/voice/sim_report.py --n 60 --priors mic --pitches headphones
poetry run pytest tests/unit/voice_session/test_replay.py -q
# export a real session (prod Firestore, read-only; needs Andrew's credentials):
python scripts/voice/export_session_fixture.py --email <e> --session <id> --out tests/unit/voice_session/fixtures/<name>.json
```

## What the simulation models (`simulate.py`)

**Singers.** Each singer has a true hypothesis (type × shift) and cycles over all 30. Songs come from a
synthetic pool. Every song is sung in the singer's likely octave. The position and difficulty answers are
sampled from the model's own likelihoods, with 15% uniformly random answers.

**Priors** (`SimPrior`):
- `self_register`: a 3c register-only self report.
- `uniform`: a flat prior.
- `mic`: the 3c mic test.
  - One N(−1.0, 2.5) error is applied to the whole reading, so the mic reads low on average.
  - Comfort is saved as an 11-key band around the measured centre (nomad-mobile #20, `MIN_COMFORT_KEYS`).
  - The type comes from the 3c centre rule (`classifyVoice`, ported as `classify_3c`).
  - It then goes into `Posterior.from_profile` as `method="mic"`.
  - With this error, the mic names the true type for 59% of singers.

**Pitch** (`SimPitch`):
- `none`: no pitch summary.
- `headphones` (σ 0.7) and `open` (σ 1.5):
  - Percentiles are the song's, at the sung octave, clipped into the singer's chest (full) range, plus noise.
  - Held notes are the clipped p10/p90 ∓ 1.
  - `voiced_s` is drawn from 60–180.
  - With probability 0.3, one falsetto note at chest top + 3 leaks into `held_high`.

**Scoring.**
- `exact_at[k]` and `adjacent_at[k]` score the result after k songs (`SimResult.at(k)`, read from `trace`).
  A session that stopped earlier keeps its final result.
- `confident_within[k]` is the share that stopped "confident" within k songs.
- Sessions are capped at 10 songs.

## Baseline: simulation (seed 20261025, n = 300 per row)

Values are exact / exact-or-adjacent / stopped-confident, after k = 3 · 4 · 5 · 10 songs.

| prior | pitch | exact @3·4·5·10 | adjacent @3·4·5·10 | confident ≤3·4·5·10 |
|---|---|---|---|---|
| self_register | none | 66 · 73 · 75 · 77 % | 94 · 96 · 98 · 99 % | 1 · 39 · 69 · 99 % |
| self_register | headphones | 69 · 70 · 70 · 71 % | 99 · 99 · 99 · 99 % | 65 · 91 · 98 · 100 % |
| self_register | open | 77 · 77 · 79 · 79 % | 100 · 100 · 100 · 100 % | 70 · 91 · 99 · 100 % |
| uniform | none | 60 · 67 · 70 · 73 % | 91 · 95 · 96 · 98 % | 0 · 32 · 58 · 98 % |
| uniform | headphones | 68 · 68 · 69 · 70 % | 100 · 100 · 100 · 100 % | 54 · 84 · 97 · 100 % |
| uniform | open | 69 · 72 · 73 · 73 % | 99 · 100 · 100 · 100 % | 58 · 87 · 98 · 100 % |
| mic | none | 66 · 70 · 70 · 71 % | 94 · 95 · 97 · 99 % | 10 · 51 · 77 · 98 % |
| mic | headphones | 75 · 76 · 76 · 76 % | 100 · 100 · 100 · 100 % | 67 · 90 · 100 · 100 % |
| mic | open | 70 · 71 · 71 · 71 % | 100 · 100 · 100 · 100 % | 73 · 95 · 99 · 100 % |

At k = 5 with the mic prior and headphones, the 24% of singers who get the wrong type are all off by one
type. True → reported, counts out of 300:

| true → reported | count |
|---|---|
| baritone → tenor | 14 |
| tenor → baritone | 11 |
| mezzo → soprano | 10 |
| mezzo → alto | 9 |
| soprano → mezzo | 9 |
| alto → mezzo | 6 |
| tenor → alto | 6 |
| bass → baritone | 5 |
| alto → tenor | 2 |

With the falsetto leak set to 0, exact@5 is 77% (76% with the leak).

## Baseline: Andrew's first session (2026-10-25, Pixel)

**Fixture:** `tests/unit/voice_session/fixtures/andrew_2026_10_25.json`.

**Session inputs:**
- Prior: the mic profile (bass, 40–69, with the old stretched comfort 42–67; the corrected 11-key band
  would be about 50–60).
- Setup: headphones, standing, "a bit warm".
- 4 songs, every one rated "comfortable"; song 2 was known "barely" and stopped early.
- Demand comes from the voice.db analysis (run 20261025-122002) for each song.

**Replay check:** the replay reproduces the live result exactly: "Probably Baritone", low confidence,
46–70 (A#2–A#4), comfortable 51–65 (D#3–F4).

**Prior** (mic: bass at 4×, its neighbour baritone at 2×, the others at 1×):

| bass | baritone | tenor | alto | mezzo | soprano |
|---|---|---|---|---|---|
| .40 | .20 | .10 | .10 | .10 | .10 |

Prior comfort: 51.8–65.8.

**Replay trace:**

| after song | sung (held · p10/p50/p90) | type | top_p | pair_p | label | comfort | full | P(bass / bari / tenor) |
|---|---|---|---|---|---|---|---|---|
| 1 | 49–59 · 51.3/56.3/57.2 | bass | .64 | .95 | low | 47–61 | 42–66 | .64 / .31 / .05 |
| 2 | 46–57 · 48.5/54.0/60.9 | bass | .63 | .96 | low | 47–61 | 42–66 | .63 / .33 / .04 |
| 3 | 52–69 · 56.2/60.4/69.1 | baritone | .71 | 1.00 | low | 51–65 | 46–70 | .00 / .71 / .29 |
| 4 | 47–61 · 48.9/56.7/58.9 | baritone | .72 | 1.00 | low | 51–65 | 46–70 | .00 / .73 / .27 |

Song 2 was known "barely" and stopped early. Song 3's notes at MIDI 69 were all falsetto.

With song 3's falsetto relabelled (`held_high` 68, percentiles kept), the result is almost the same:
song 3 gives baritone .73 and song 4 gives baritone .74 / tenor .25, still low. With the mic prior
named baritone instead of bass, the result after 4 songs is the same: baritone .73, low.

## Diagnosis

1. **The "low" label after 4 songs comes from the stability gate.**
   - After song 4 the posterior alone would earn "medium": the baritone+tenor pair is at 0.99, above 0.85.
   - `_enough_evidence` also needs the comfort estimate to have moved at most 2 semitones over the last 2
     songs. Song 3 flipped bass → baritone, and the comfort estimate jumped 47–61 → 51–65 (4 semitones).
   - With 4 songs, the window still contains that jump.
   - The flip came late because the mic prior put bass at 4×, and songs 1–2 told the model almost nothing
     about type.
2. **Comfortable answers carry almost no type information** (plan finding 1).
   - All four songs were "comfortable" and sat at MIDI 49–61. That region is comfortable for bass+2,
     baritone and tenor−2 alike.
   - The pitch only bounds the full range (`likelihood_pitch` checks that held notes fit).
   - Held 46–69 fits baritone (45–69) and tenor−2 (46–70) equally. That is why tenor stays at ~0.27 and
     top_p never reaches 0.75.
   - Andrew's sung tessitura (p50 ≈ 56) sits at baritone's comfort centre (57) and well below tenor's (60),
     but the model never looks at it. E2's tessitura likelihood is the lever.
3. **Falsetto is not what kept the result low in today's model.** Relabelling the 69s moves top_p by
   only +0.02, because the evidence that ruled out bass was the held 68–69 either way.
   - The falsetto does inflate the full and comfort estimates. Bass+2 tops out at 66, so even a chest 68
     excludes bass.
   - Chest-only pitch (C1) still matters for the measured comfort (D1). Song 3's p90 of 69.1 is falsetto.
4. **Comfort is the model's type-mean (51–65), not what he sang.**
   - The comfortable, known songs (1, 3, 4) had p10/p90 of 51.3–57.2, 56.2–69.1 and 48.9–58.9.
   - Their voiced_s-weighted means are 52.6–62.2, but song 3's p90 includes the falsetto.
   - Without song 3, the band is 50.3–57.9. The E5 replay target is (49, 59) ± 2.
   - D1 (measured comfort) addresses this, and it needs chest-only pitch to be right.
5. **The simulation is far more optimistic than the real session, and the E5 gates already pass.**
   - Mic prior + headphones gives exact@5 76% (gate ≥ 70%), adjacent@5 100% (≥ 92%) and confident≤5 100%
     (≥ 50%). Open-mic exact@5 is 71% (≥ 60%).
   - The simulated singer answers from the model's own likelihoods. Because EIG picks songs at the type
     boundaries, it often answers too_high / too_low. Andrew answered "comfortable" 4/4.
   - In the sim, pitch mostly speeds up stopping, not accuracy. exact@10 with pitch is 70–79%, versus
     71–77% without.
   - Sessions stop "confident" at 3–5 songs while about a quarter are one type off, so the sim is
     confidently wrong. Pitch narrows the full range, which settles the comfort estimate and passes the
     stability gate, but it can't separate adjacent types (the error table is all adjacent pairs).
   - The mic prior caps exact accuracy early: with headphones, exact is 75% at 3 songs and 76% at 10.

## Recommendations for E2–E5 (no weights changed in E1)

1. **Add tessitura evidence (E2) before re-weighting anything else.** It is the only change that separates
   baritone from tenor on "comfortable" answers, both in the replay and in the sim's error table.
2. **Make the sim harder before tuning against it (E5).** Otherwise the 70/92/50 gates are already met and
   the grid can't tell values apart. Options:
   - Bias the simulated answers toward "comfortable". For example, with p = 0.3 a stretch song within
     2–3 semitones of comfort is answered "comfortable" (Andrew 4/4).
   - Add a "confidently wrong" metric (stopped confident and not exact) to the report, and gate on it.
   - Keep the Andrew replay gate as the binding real-world check.
3. **The stability gate dominates the label for late flips.** E3's centred, floored mic prior should make
   early songs move the estimate less abruptly. If the replay still ends "low" only because of the window,
   consider letting the stability window count from the first attempt with usable pitch.
