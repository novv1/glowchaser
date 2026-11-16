# Architecture notes

These notes cover the parts of GlowChaser that are not obvious from the code, and the
reasoning behind choices that look strange until you know the constraint.

---

## 1. Design constraint

The system must produce an **interpretable, calibrated probability** on a free-tier data
budget, for a user who will make a decision two hours in advance and never see the model.

That rules out an end-to-end learned scorer. There is no labelled dataset large enough to
train one (see §4 — the whole calibration set is 366 records), and a black-box output is
useless to a photographer who wants to know *why* the forecast changed.

So every dimension is a hand-built scoring function with a physically motivated shape, and
the only learned component is the **final probability mapping** from score to frequency,
which is fitted on observed outcomes. The score itself is never learned.

---

## 2. The eight dimensions

| # | Weight | Dimension | What it captures |
|---|---|---|---|
| 1 | 0.23 | Mid/high cloud as canvas | Whether there is a mid or high deck to catch light, penalised for low cloud blocking it |
| 2 | 0.30 | Cloud along the optical path | Cloud conditions between observer and target, weighted by distance |
| 3 | 0.10 | Near-surface precipitation | Rain now and in the previous 3 h |
| 4 | 0.11 | Visibility / particulate | Surface visibility and PM2.5 |
| 5 | 0.03 | Cloud motion trend | Change in canvas availability over the last 3 h |
| 6 | 0.01 | Pressure tendency | 3-hour pressure change, as a proxy for synoptic evolution |
| 7 | 0.12 | Aerosol optical depth | CAMS AOD at the observer and along the path |
| 8 | 0.10 | Upper-level moisture | RH at 300 and 500 hPa, gated by high cloud cover |

Weights sum to 1.0. They were set by grid search against the backtest, then frozen —
not re-fitted per query.

**Why dimension 2 outweighs dimension 1.** The canvas matters, but the *path* matters more.
A perfect cirrus deck behind 60 km of low stratus produces nothing, and that failure mode is
far more common in the Pearl River Delta than a missing canvas. The weight reflects the
empirical cost of each error.

**Why dimensions 5 and 6 are nearly worthless.** They are included because they cost
nothing (already in the forecast payload) and occasionally break ties, but their combined
weight is 0.04. If the model were refitted from scratch they would probably be dropped.

---

## 3. Illumination geometry

A cloud is only a canvas if it is still in sunlight. For a layer at height `h`:

```
b(h) = acos( R / (R + h) )
```

The sun must be above `-b(h)` in elevation for that layer to be lit. This is computed
**per pressure level**, using the real geopotential height of each level, not a single
column-average.

The difference is not academic. At 10 km, `b ≈ 3.2°`; at 4 km, `b ≈ 2.0°`. During the
10–15 minutes after sunset, that gap decides whether the high cirrus is still burning while
the mid deck has gone dark — which is exactly the visual signature of a good afterglow.

The sightline is traced the same way: for each layer, solve for the ground distance at
which the observer's line of sight intersects that layer's cloud shell. That distance is
what dimension 2 uses to weight cloud along the path, and what the frontend uses to draw
which cloud band is blocking the view and how far away it is.

---

## 4. Probability calibration

This is the part I am least sure about, and the part that took the most work.

### Method

1. Bin the raw score.
2. Compute the observed event rate in each bin.
3. Fit a monotone mapping through those rates with **PAVA** (pool adjacent violators), so the
   probability never decreases as the score rises.
4. Rescale to the base rate of the development set.

### Two calibration sets, deliberately kept separate

| Set | Period | Records | Event base rate | Held-out Brier |
|---|---|---|---|---|
| Shanghai | 2022-06 – 2023-12 | 555 days | 27.7% | 0.269 → **0.231** (2023 H2) |
| Pearl River Delta | 2026-04 – 2026-09 | 366 days | 77.6% | 0.255 → **0.170** (Aug–Sep) |

The PRD climatology baseline is 0.174, so the recalibrated curve is **better than
climatology** on the held-out window. The older Shanghai curve scored 0.289 on PRD data —
systematically too low, because it was fitted where glow events happen 27.7% of the time,
applied where they happen 77.6% of the time.

**Label definitions differ between the two sets**, which is why they are not merged:

- Shanghai: "a burn worth recording" — a human judgement.
- PRD: "illuminated canvas ≥ 30%" — a satellite-derived necessary condition, and therefore
  a *broader* label. The verdict thresholds had to be re-specified for the new semantics.

The low end of the PRD curve (below 30 score) has **no local samples at all** — there were
zero such days in the set. The 20% anchor at zero score is a conservative extrapolation
placed between the Shanghai value (2.5%) and the first PRD bin (60.8%). It exists to stop
extremely poor days from being over-predicted, and it is the weakest point in the mapping.

---

## 5. Multi-model consensus

Four deterministic models (ECMWF, GFS, ICON, CMA) plus a 50-member ensemble. The ensemble
supplies a P10/P50/P90 band on mid/high cloud cover, which is reported alongside the point
forecast rather than folded into the score.

Models are combined with fixed weights, not by averaging scores. Averaging scores across
models hides disagreement; taking the weighted mean of *inputs* and scoring once keeps the
disagreement visible, which matters because model disagreement is itself a risk signal.

---

## 6. Engineering under a hard quota

The data providers impose a daily request limit, and the system hits it. Four layers:

1. **Tiered handling of HTTP 429.** A daily-quota exhaustion gives up immediately — retrying
   cannot help and only burns the remaining allowance. A rate-limit 429 backs off instead.
2. **In-process cooldown.** After a quota exhaustion, requests are suppressed for a window.
   Nothing sleeps; nothing queues; the process simply returns the degraded answer.
3. **On-disk cache with TTL.** Three hours by default, overridable. On a cache hit the report
   is served with its generation timestamp, so the user knows how stale it is.
4. **Reuse of a single fetch.** One sounding call produces the profile used in three
   different views (grid, cross-section, standalone plot). Naively this would be three calls.

When the quota is gone and the cache is cold, the frontend shows an error card with the
reason and the time of the last successful report. It does not show a fabricated forecast.

---

## 7. What I would do differently

- **Dimension 1 needs a different functional form.** The current version multiplies a
  cloud-quality term by an optical-path penalty. Those two effects are not separable on 366
  records, so the coefficients are unstable. A hierarchical model with a shared shape and
  per-region offsets would probably fit better than one global grid search.
- **The calibration should be online.** Every day produces a new labelled record
  (`data/glow_labels.csv` is append-only). Refitting is currently manual. A proper
  implementation would refit on a rolling window and monitor for drift.
- **The 20% anchor at zero score is a guess.** It should be replaced by real samples from
  bad-weather days, which means deliberately predicting on days the system is confident
  about — currently it is only evaluated on days someone bothered to record.
- **Satellite nowcast resolution.** At 26.9 km/pixel over the PRD, the nowcast cannot see
  individual cloud streets. Fusing a geostationary product with a higher-resolution
  polar orbiter would help, at the cost of latency.
