# Voice session tuning

How well the voice session (`karaoke_decide/voice_session/`) finds a singer's voice type within 3–5 songs,
measured two ways:

1. **Simulated singers.** `scripts/voice/sim_report.py` runs prior × pitch × k.
2. **Replays of real sessions.** `karaoke_decide.voice_session.replay` replays fixtures exported with
   `scripts/voice/export_session_fixture.py`.

Plan: nomadkaraoke workspace `docs/archive/2026-10-25-mobile-phase3d-tuning-batch-1-plan.md` (Part E).
This page records the **baseline before any tuning (Task E1, v0.31.x weights)**, then the
[tuning batch 1 results (E2–E5, D1, v0.32.0)](#tuning-batch-1-e2e5-d1-v0320) below it.

## How to run

```bash
poetry run python scripts/voice/sim_report.py              # both answer styles, seed 20261025, n 300 per row, ~3 min
poetry run python scripts/voice/sim_report.py --n 60 --priors mic --pitches headphones --styles realistic
poetry run python scripts/voice/sim_grid.py                # coordinate-wise weight search against every gate, ~3 min on 8 cores
poetry run pytest tests/unit/voice_session/test_replay.py -q -s   # prints Andrew's replay traces
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

## Tuning batch 1 (E2–E5, D1, v0.32.0)

### What changed

| task | change | where |
|---|---|---|
| E2 | **Tessitura**: a song rated comfortable counts its sung p10/p50/p90 against each hypothesis's comfortable band: `σ((c_hi − p90 + 1)/TESS_TEMP)·σ((p10 − c_lo + 1)/TESS_TEMP)·exp(−(p50 − centre)²/(2·TESS_SD²))`, weighted `W_TESSITURA · K_KNEW`. | `model.likelihood_tessitura` |
| E3 | **Range-shaped prior**: a mic or session profile gives `PRIOR_FLOOR + (1 − PRIOR_FLOOR)·exp(−(centre(h) − (profile centre + MIC_BIAS))²/(2·PRIOR_SD²))` over all 30 hypotheses. The mic is centred on its comfort band (+1). A session is centred on its saved **full range** (`low_midi`..`high_midi`, bias 0; review fix, ruling 6). The named type is no longer used. Self reports are unchanged. | `model.Posterior.from_profile` |
| E4 | **Selection**: `like · know · ((1 − SEP_WEIGHT)·EIG + SEP_WEIGHT·separation) − stratum penalty`. `separation` is the TV distance between the top two types' answer distributions × their mass; `like = LIKE_BASE + (1 − LIKE_BASE)·max(pref, loved)`. The first song's familiar pool is taste-weighted. | `select.choose_next` |
| D1 | **Measured comfort**: once ≥ 2 songs (≥ 60 s voiced) were rated comfortable and easy, known better than "barely", with usable pitch, comfort is measured from them. Start from the voiced-seconds-weighted mean of their p10/p90, each kept inside that song's held notes. Widen it to at least 11 keys around its centre and to cover each counted song's p10..p90 (ruling 7). Shift it inside the full estimate. `comfort_source` says which. | `comfort.comfort_band`, `stopping.summary` |
| E5 | **Realistic simulated singers** and a **confidently-wrong** metric (below), four rulings, then a weight grid. | `simulate.py`, `stopping.py`, `model.py`, `weights.py` |

### Realistic simulated singers (`answer_style="realistic"`)

The E1 sim answered from the model's own likelihoods. Because selection probes the type boundaries, it said
too_high / too_low far more readily than real singers do. Andrew said "comfortable" 4/4, including to a song he
could only finish in falsetto.

- A song whose sung body (p10..p90 at the sung octave) stays inside the singer's **full** chest range is answered
  "comfortable" with probability `REALISTIC_COMFORT_P = 0.8`; otherwise it gets the model-likelihood answer.
- Songs that leave the full range always get the model-likelihood answer, so too_high / too_low come only from them.
- Difficulty is always the model-likelihood answer, so a song past the comfortable band is often "comfortable,
  but a stretch", as Andrew's song 3 was.
- The position likelihood was not tuned, so the singer's fallback answers are the v0.31 response model, not a
  moving target.
- `answer_style="model"` (the default for `simulate`/`run_population`) keeps the old behaviour, so the existing
  gates (`test_simulated_users_converge`) keep their meaning.

**`confidently_wrong_within[k]`** is the share of singers whose session stopped "confident" (medium or high)
within k songs with the wrong exact type.

### Rulings (beyond the plan's grid)

1. **Tessitura and measured comfort only from songs rated comfortable *and easy*.** "Comfortable, but a stretch"
   means the song reached past the comfortable band. Andrew's song 3 was exactly that: chest to 68, falsetto to
   69, p90 69.1 still in the percentiles after relabelling. Counting it pulled him toward tenor and would have
   set comfort to 53–62. Same rule in `model.tessitura_applies` and `comfort.counts_for_comfort`.
2. **No octave shift of the pitch.** The plan said to "shift back by the sung octave". The percentiles are already
   the singer's own notes in the octave they sang, so shifting them by `sung_octave` would move a baritone's
   octave-down female song into mezzo range. They are compared as sung (`test_tessitura_uses_the_pitch_as_sung_in_any_octave`).
3. **Stability: a measured comfort settles the range.** E1 found that "low" after 4 songs came from the 2-song
   stability window on the *model's* comfort estimate, which jumps when the MAP type flips. Now
   `_enough_evidence` also passes when `measured_comfort` exists, since that is what the result shows and it
   doesn't move with the type. `MIN_RANGE_ATTEMPTS = 3` still applies.
4. **Early "between" stops need a clear lean**: within `MEDIUM_LEAN_UNTIL = 5` *counting* songs, "medium" also needs
   the top type ≥ `MEDIUM_MIN_TOP = 0.7`. Counting songs means `len(state.evidence)`; excluded attempts don't count.
   After 5 songs a settled adjacent pair stops as before.
   - Adjacent types share ranges at the ends of their shifts: baritone+1/+2 ≡ tenor−2/−1, alto+2 ≡ mezzo−2,
     mezzo+1/+2 ≡ soprano−2/−1. A third of the 30 hypotheses are a coin flip on the exact type, so a ~50/50
     pair is most often the confidently-wrong case.
   - Without this rule, confidently-wrong@5 was 23–27% under every weight tried.
   - Applying the floor at every length would have regressed the existing gate (self-register, quiz-only:
     confident≤10 98% → 68%, gate ≥ 80%). Hence the 5-song limit.
5. **The held top is robust to one missed falsetto note.** The upper bound of `likelihood_pitch` never drops
   below `HELD_HIGH_FALSETTO_P = 0.3`. A single falsetto note in `held_high` (the sim leaks one into 30% of songs;
   Andrew's song 3) was ruling the true type out at `W_PITCH`². It drove most remaining confident-wrong answers,
   all of them "one type too high". The lower bound is unchanged, since falsetto only goes up.
6. **A saved session profile seeds the next session from its full range** (review fix). Since D1, a session's
   saved comfort can be measured, and it isn't centred on the type: Andrew's 48–59 is centred at 53.5, a bass
   centre. Centring on it would have started his next session leaning bass (≈ bass 4.07 / baritone 3.51 /
   tenor 1.92). The saved `low_midi`..`high_midi` is the posterior-mean full range, so the session prior centres
   there. The mic prior keeps its comfort-band centre. Tests round-trip summary → `session_profile` →
   `from_profile` and keep the top type: Andrew (baritone), a tenor who sings low, a soprano who sings low.
7. **The measured band covers the songs that measured it** (review fix). A mean of p10/p90 is narrower than the
   songs behind it. Song fit (`vocal_range.fit`, measured tier, tolerance 0) needs a song's p10..p90 inside the
   band, so his own comfortable songs would have stopped being "singable".
   - The band is at least `MIN_COMFORT_KEYS = 11` keys (inclusive) around the measured centre, like the mic
     band in nomad-mobile #20.
   - It also covers every counted song's p10..p90 (floor/ceil), because the strict float comparison would
     otherwise drop Andrew's song 4 (48.9–58.9) from a centred 49–59.
   - It is shifted, not cut, to sit inside the full estimate.

### Gates (seed 20261025, n = 300, realistic answers, mic prior)

| gate | E1 baseline (v0.31) | v0.32.0 | |
|---|---|---|---|
| headphones exact@5 ≥ 70% | 70% | **81%** | pass |
| headphones adjacent@5 ≥ 92% | 100% | **100%** | pass |
| headphones confident≤5 ≥ 50% | 99% | **58%** | pass |
| headphones confidently-wrong≤5 ≤ 10% | 30% | **5%** | pass |
| open-mic exact@5 ≥ 60% | 74% | **77%** | pass |
| Andrew replay (69+ falsetto) after 4: baritone, medium/high, comfort (49, 59) ± 2 | baritone .74, low, 51–65 | **baritone .82, high, 48–59** | pass |
| existing: model answers, self-register, no pitch (seed 20261022, n 200): confident≤10 ≥ 80%, adjacent ≥ 90%, exact ≥ 70% | pass | **98% / 100% / 82%** | pass |

The E1 sim's 99% confident within 5 songs came with 30% of singers confidently wrong. v0.32 stops later (58%)
and is wrong a sixth as often.

### Second seeds (not gated)

The current weights on other seeds (`sim_grid.py --evaluate --seed <s>`, n = 300; the existing gate always uses
seed 20261022). The margins hold:

| seed | exact@5 (≥ 70%) | adjacent@5 (≥ 92%) | confident≤5 (≥ 50%) | wrong≤5 (≤ 10%) | open exact@5 (≥ 60%) |
|---|---|---|---|---|---|
| 20261025 (gate) | 81.0% | 100% | 58% | 5.0% | 77% |
| 20261026 | 80.3% | 100% | 59% | 5.3% | 77% |
| 20261027 | 79.0% | 100% | 61% | 5.0% | 76% |

### Before / after: simulation (seed 20261025, n = 300 per row, sessions capped at 10)

Columns are exact / exact-or-adjacent / stopped confident / stopped confident and wrong, after k = 3 · 4 · 5 · 10 songs.
The "before" rows are the E1 code (v0.31 weights) run with the same realistic-answer logic.

**Realistic answers, before (v0.31):**

| prior | pitch | exact | adjacent | confident | confidently wrong |
|---|---|---|---|---|---|
| self_register | none | 67 · 69 · 71 · 72 % | 93 · 95 · 98 · 99 % | 0 · 42 · 66 · 97 % | 0 · 14 · 20 · 26 % |
| self_register | headphones | 71 · 69 · 70 · 70 % | 99 · 100 · 100 · 100 % | 68 · 92 · 99 · 100 % | 22 · 29 · 30 · 30 % |
| self_register | open | 72 · 75 · 75 · 75 % | 100 · 100 · 100 · 100 % | 66 · 88 · 97 · 100 % | 20 · 23 · 24 · 25 % |
| uniform | none | 57 · 62 · 65 · 69 % | 89 · 92 · 95 · 98 % | 0 · 32 · 56 · 97 % | 0 · 14 · 21 · 30 % |
| uniform | headphones | 66 · 68 · 69 · 69 % | 99 · 99 · 99 · 99 % | 56 · 85 · 97 · 100 % | 18 · 26 · 30 · 31 % |
| uniform | open | 65 · 67 · 68 · 68 % | 99 · 99 · 99 · 99 % | 64 · 87 · 98 · 100 % | 22 · 29 · 32 · 32 % |
| mic | none | 67 · 70 · 72 · 73 % | 97 · 97 · 99 · 99 % | 10 · 57 · 77 · 98 % | 2 · 17 · 20 · 26 % |
| mic | headphones | 70 · 71 · 70 · 70 % | 100 · 100 · 100 · 100 % | 68 · 88 · 99 · 100 % | 23 · 27 · 30 · 30 % |
| mic | open | 72 · 74 · 74 · 74 % | 98 · 99 · 99 · 99 % | 76 · 92 · 99 · 100 % | 21 · 24 · 25 · 26 % |

**Realistic answers, after (v0.32.0):**

| prior | pitch | exact | adjacent | confident | confidently wrong |
|---|---|---|---|---|---|
| self_register | none | 65 · 68 · 70 · 75 % | 95 · 97 · 98 · 99 % | 1 · 9 · 23 · 95 % | 0 · 1 · 2 · 23 % |
| self_register | headphones | 73 · 78 · 82 · 81 % | 99 · 100 · 100 · 100 % | 26 · 49 · 60 · 100 % | 2 · 5 · 7 · 19 % |
| self_register | open | 73 · 76 · 78 · 78 % | 99 · 100 · 100 · 100 % | 26 · 46 · 59 · 100 % | 2 · 4 · 5 · 22 % |
| uniform | none | 58 · 61 · 68 · 71 % | 88 · 93 · 96 · 99 % | 0 · 4 · 14 · 93 % | 0 · 0 · 1 · 26 % |
| uniform | headphones | 71 · 75 · 78 · 78 % | 98 · 100 · 100 · 100 % | 23 · 43 · 56 · 100 % | 2 · 5 · 6 · 22 % |
| uniform | open | 66 · 74 · 78 · 78 % | 98 · 99 · 99 · 100 % | 27 · 44 · 54 · 100 % | 3 · 4 · 6 · 22 % |
| mic | none | 68 · 72 · 74 · 77 % | 95 · 97 · 98 · 99 % | 4 · 19 · 33 · 97 % | 1 · 3 · 5 · 22 % |
| mic | headphones | 75 · 82 · 81 · 80 % | 100 · 100 · 100 · 100 % | 29 · 46 · 58 · 100 % | 2 · 4 · 5 · 20 % |
| mic | open | 74 · 77 · 77 · 79 % | 100 · 100 · 100 · 100 % | 33 · 48 · 61 · 100 % | 5 · 6 · 6 · 21 % |

**Model answers (old style), before (v0.31):**

| prior | pitch | exact | adjacent | confident | confidently wrong |
|---|---|---|---|---|---|
| self_register | none | 66 · 73 · 75 · 77 % | 94 · 96 · 98 · 99 % | 1 · 39 · 69 · 99 % | 0 · 9 · 15 · 23 % |
| self_register | headphones | 69 · 70 · 70 · 71 % | 99 · 99 · 99 · 99 % | 65 · 91 · 98 · 100 % | 23 · 27 · 29 · 29 % |
| self_register | open | 77 · 77 · 79 · 79 % | 100 · 100 · 100 · 100 % | 70 · 91 · 99 · 100 % | 18 · 20 · 21 · 21 % |
| uniform | none | 60 · 67 · 70 · 73 % | 91 · 95 · 96 · 98 % | 0 · 32 · 58 · 98 % | 0 · 10 · 19 · 26 % |
| uniform | headphones | 68 · 68 · 69 · 70 % | 100 · 100 · 100 · 100 % | 54 · 84 · 97 · 100 % | 18 · 27 · 30 · 30 % |
| uniform | open | 69 · 72 · 73 · 73 % | 99 · 100 · 100 · 100 % | 58 · 87 · 98 · 100 % | 17 · 24 · 27 · 27 % |
| mic | none | 66 · 70 · 70 · 71 % | 94 · 95 · 97 · 99 % | 10 · 51 · 77 · 98 % | 3 · 16 · 21 · 28 % |
| mic | headphones | 75 · 76 · 76 · 76 % | 100 · 100 · 100 · 100 % | 67 · 90 · 100 · 100 % | 17 · 23 · 24 · 24 % |
| mic | open | 70 · 71 · 71 · 71 % | 100 · 100 · 100 · 100 % | 73 · 95 · 99 · 100 % | 21 · 28 · 29 · 29 % |

**Model answers (old style), after (v0.32.0):**

| prior | pitch | exact | adjacent | confident | confidently wrong |
|---|---|---|---|---|---|
| self_register | none | 71 · 73 · 76 · 79 % | 95 · 98 · 97 · 100 % | 1 · 10 · 30 · 99 % | 0 · 0 · 0 · 21 % |
| self_register | headphones | 76 · 79 · 80 · 83 % | 99 · 100 · 100 · 100 % | 30 · 52 · 61 · 100 % | 2 · 4 · 5 · 17 % |
| self_register | open | 77 · 79 · 81 · 81 % | 99 · 100 · 100 · 100 % | 26 · 46 · 58 · 100 % | 1 · 3 · 5 · 19 % |
| uniform | none | 62 · 65 · 66 · 75 % | 89 · 92 · 94 · 98 % | 0 · 8 · 20 · 96 % | 0 · 1 · 2 · 24 % |
| uniform | headphones | 73 · 77 · 82 · 81 % | 99 · 99 · 100 · 100 % | 18 · 40 · 54 · 100 % | 1 · 3 · 4 · 19 % |
| uniform | open | 73 · 76 · 79 · 80 % | 99 · 99 · 100 · 100 % | 19 · 37 · 51 · 100 % | 2 · 3 · 3 · 20 % |
| mic | none | 68 · 73 · 75 · 77 % | 96 · 97 · 99 · 99 % | 3 · 20 · 38 · 97 % | 0 · 2 · 4 · 23 % |
| mic | headphones | 76 · 79 · 84 · 84 % | 100 · 100 · 100 · 100 % | 30 · 49 · 59 · 100 % | 2 · 4 · 4 · 16 % |
| mic | open | 73 · 77 · 76 · 77 % | 100 · 100 · 100 · 100 % | 27 · 45 · 57 · 100 % | 5 · 6 · 7 · 23 % |

Quiz-only from a flat start (realistic answers, not gated): exact@3·4·5 = 58 · 61 · 68% (v0.31: 57 · 62 · 65%).

### Weight grid (`scripts/voice/sim_grid.py`)

Coordinate-wise over the plan's grid, objective exact@5 (realistic, mic, headphones), keeping only settings that
pass every gate above. The four rulings were in place first. Every setting tried passed all gates (38 of 38).

| knob | grid | v0.31 | E2–E4 start | v0.32.0 |
|---|---|---|---|---|
| `W_PITCH_HEADPHONES` | 1.5 · 2 · 3 | 1.5 | 2.0 | **1.5** |
| `W_PITCH_OPEN` | (not gridded) | 0.6 | 1.0 | **1.0** |
| `W_TESSITURA` | 1 · 2 · 3 | – | 1.0 | **1.0** |
| `TESS_SD` | 2.5 · 3 · 4 | – | 3.0 | **3.0** |
| `PRIOR_SD` | 2.5 · 3 · 4 | – | 3.0 | **3.0** |
| `SEP_WEIGHT` | 0.3 · 0.5 · 0.7 | – | 0.5 | **0.3** |
| `LIKE_BASE` | 0.5 · 0.6 · 0.8 | – (`PREF_WEIGHT` 0.03) | 0.6 | **0.6** |
| `PRIOR_FLOOR`, `MIC_BIAS`, `TESS_TEMP`, `HELD_HIGH_FALSETTO_P`, `MEDIUM_MIN_TOP`, `MEDIUM_LEAN_UNTIL` | (not gridded) | – | – | 0.25, +1 mic / 0 session, 1.0, 0.3, 0.7, 5 |

Over the whole grid exact@5 ranged 74–81%. Changes of 1–2 points (3–6 singers in 300) are seed noise.
`W_TESSITURA` 2–3 makes Andrew more certain (baritone .92–.96) but raises sim confidently-wrong to 8–10%.
`SEP_WEIGHT` 0.7 costs the existing gate 4 points of confident≤10.

### Andrew's replay after tuning (v0.32.0)

69+ relabelled falsetto (`held_high` capped at 68, percentiles kept). This is the gate (`test_andrew_replay`):

| after song | type | top_p | pair_p | label | comfort | source | full |
|---|---|---|---|---|---|---|---|
| 1 | baritone | .62 | .91 | low | 49–63 | model | 44–68 |
| 2 | baritone | .62 | .92 | low | 48–62 | model | 43–67 |
| 3 | baritone | .69 | .98 | low | 50–64 | model | 45–69 |
| 4 | baritone | .82 | .98 | **high** | **48–59** | measured | 45–69 |

As recorded, falsetto left in (what today's app sends; not gated):

| after song | type | top_p | pair_p | label | comfort | source | full |
|---|---|---|---|---|---|---|---|
| 1 | baritone | .62 | .91 | low | 49–63 | model | 44–68 |
| 2 | baritone | .62 | .92 | low | 48–62 | model | 43–67 |
| 3 | baritone | .67 | .99 | low | 51–65 | model | 46–70 |
| 4 | baritone | .80 | .98 | **high** | **48–59** | measured | 45–69 |

- **Baritone from song 1.** The range-shaped prior reads the mic band (42–67, centre 54.5 + 1) instead of
  "bass ×4".
- **Song 3 still dips** because it is rated comfortable on a song whose demand reaches 69. Its tessitura no
  longer counts (a stretch), and the held 68/69 no longer rules out baritone.
- **Song 4 settles it.** Measured comfort from songs 1 and 4 is available, so the stability gate is met, and
  top_p .82 ≥ .75 gives "high".
  - Their mean band is 50.3–57.9, widened to 11 keys around 54.1 (49–59) and to cover song 4's 48.9–58.9.
  - The result is 48–59.
- **With song 3 excluded** ("Don't count this one"; `test_andrew_replay_without_the_falsetto_song_is_still_baritone`)
  the replay ends baritone .70, comfort 48–59 measured. The label is "low": .70 is just under the 0.7 lean
  floor within 5 songs.
- **Song 2** (known "barely") never counts toward the measured comfort.

### Open issues and next levers

1. **The exact type is ambiguous for a third of the hypothesis space.** Adjacent types share ranges at the ends of
   their shifts. The ceiling on exact accuracy is about 83%, and most remaining errors are those pairs. A
   type-free result (range + "between") would be the honest fix.
2. **Confidently-wrong rises after 5 songs** (≈ 20% at 10). By design, a settled pair stops "between X and Y" after
   5 songs, and the metric counts the 50% of those where the top type is the other one.
3. **The realistic sim is still a model.** It doesn't simulate falsetto-rated songs, octave errors in the demand
   analysis (Andrew's song 4 was sung 10 semitones below its demand), or wrong "knew" answers. Andrew's second
   session (manual gate 3) is the real check.
4. **C1 (chest-only pitch) is still needed.** The replay gate keeps the falsetto percentiles. The comfort comes
   out right only because the falsetto song was also rated a stretch.
5. ~~Excluded attempts (PR 1, A4) and `state.evidence`~~ **Resolved** in the rebase onto v0.31.0.
   `replay.rebuild` (which `rebuild_state` calls) skips excluded attempts before `state.evidence.append`. This is
   pinned by `test_rebuild_state_with_an_excluded_attempt_keeps_v031_semantics`.
