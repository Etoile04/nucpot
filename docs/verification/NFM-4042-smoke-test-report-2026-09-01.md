# NFM-4042 — Option-C ship smoke test report (2026-09-01)

## Summary

AC-OC-4 (prod smoke test) **FAILED** — `/api/v1/predict/energy` does not
return the fields the AC requires. The merge delivered a documentation
annotation in `energy_predictor_v3.0_metrics.json` but did NOT re-package
the v3.0 joblib artifact or extend the response schema. As a result the
production predictor still serves the pre-NFM-3953 honesty contract
(confidence=null, `energy_model_pre_grouped_cv` warning).

## Deployment

| Item | Value |
| --- | --- |
| Deploy commit | `e331da972` |
| Image tag | `nucpot-prod-api:e331da972` |
| Container | `nucpot-prod-api` / `nucpot-prod-worker` (recreated) |
| Pre-deploy tag | `bec2bcfad39827d45ceae359e731645608db399f` (old) |
| Health | API `/api/v1/health` returns 200 OK |
| Specialist artifacts in container | 10 `.joblib` files present in `/app/models/specialists/` (Al, Cr, Fe, Mn, Mo, Nb, Ru, Ti, V, Zr) + `v4.0_model_card.json` |
| Sidecar JSON in container | `rd2_label=[EXPLORATORY]`, `rd2_label_status=permanent`, `grouped_cv_summary.r2_mean=0.3111` confirmed via `docker exec` |

## Smoke test request

```bash
curl -X POST http://localhost:8001/api/v1/predict/energy \
  -H 'Content-Type: application/json' \
  -d '{"composition": {"Pu": 0.5, "Am": 0.3, "Zr": 0.2}}'
```

(OOD fictitious Pu-Am alloy as specified.)

## Smoke test response (verbatim)

```json
{
  "success": true,
  "data": {
    "predicted_energy": 0.295201,
    "confidence": null,
    "confidence_source": "random_split_r2",
    "warnings": [{
      "code": "energy_model_pre_grouped_cv",
      "message": "EnergyPredictor artifact predates the NFM-3953 grouped-CV re-evaluation; the random-split R^2 = 0.9858 may be materially optimistic. Confidence is reported as null until the artifact is re-trained to embed grouped_cv_summary and rd2_label."
    }],
    "model_version": "v3.0"
  },
  "error": null
}
```

## AC-OC-4 expected vs actual

| AC-OC-4 expectation | Actual | Status |
| --- | --- | --- |
| `confidence` = `grouped_cv_summary.r2_mean` = `0.3111` | `confidence` = `null` (legacy pre-NFM-3953 path) | ✗ FAIL |
| `warning: energy_model_exploratory` present | `warning: energy_model_pre_grouped_cv` (legacy code) | ✗ FAIL |
| `rd2_label=[EXPLORATORY]` in metadata | not surfaced in response (no schema field) | ✗ FAIL |
| `rd2_label_status: permanent` in metadata | not surfaced in response (no schema field) | ✗ FAIL |

## Root cause

### Issue 1 — joblib artifact not re-packaged

`/app/models/energy_predictor_v30.joblib` is the **older pre-NFM-3953
artifact**. Embedded metrics (verified via `docker exec`):

```
rd2_label: None
rd2_label_status: None
grouped_cv_summary: None
r2: 0.9858      ← random-split headline
cv_r2: 0.9678   ← random-KFold headline
```

The predictor code (`_predict_energy_v30` in
`apps/api/src/nfm_db/ml/prediction_service.py:821`) reads `metrics` from
this joblib artifact, NOT from the sidecar JSON. Because the joblib
lacks `rd2_label` and `grouped_cv_summary`, `_compute_energy_confidence`
falls into the **legacy fallback branch** (line 806) which returns
`confidence=None` + `energy_model_pre_grouped_cv` warning.

### Issue 2 — response schema does not surface rd2 fields

`EnergyPredictResponse` (apps/api/src/nfm_db/schemas/prediction.py:273)
only carries:

- `predicted_energy`
- `confidence`
- `confidence_source`
- `warnings`
- `model_version`

There is NO `rd2_label` or `rd2_label_status` field on the response,
even though `_compute_energy_confidence` already plumbs the rd2_label
state internally to drive the warning. Surfacing those fields would
require a schema change + threading the values through the
prediction endpoint.

### Issue 3 — annotation is in JSON sidecar, not joblib

The merge commit `09ef6c893` only added a JSON-sidecar field. The
sidecar JSON file and the joblib artifact are read by different code
paths:

| File | Read by | Carries rd2_label? |
| --- | --- | --- |
| `energy_predictor_v3.0_metrics.json` | humans, downstream docs | yes (just-added annotation) |
| `energy_predictor_v30.joblib` | `_predict_energy_v30()` runtime | **no** (still pre-NFM-3953) |

## What's needed to pass AC-OC-4

Per the issue spec, "If smoke fails (any field missing or wrong), report
back to CPO + CTO with the deviation rather than silently fixing."

This is out of RE scope. Three options:

1. **(a) Re-package the v3.0 joblib** — train (or post-process) the
   existing `energy_predictor_v30.joblib` so its embedded `metrics`
   dict carries `rd2_label="[EXPLORATORY]"` and
   `grouped_cv_summary={r2_mean:0.3111, ...}`. This would flip the
   predictor into the `energy_model_exploratory` branch and surface
   `confidence=0.3111`. The CTO hard rule "no new model training"
   arguably permits post-hoc re-packaging without retraining — needs
   explicit CPO + CTO ack.

2. **(b) Extend `EnergyPredictResponse` schema + thread rd2 fields**
   to surface `rd2_label` and `rd2_label_status` in the response body.
   Requires a small Python change (schema + endpoint return dict) +
   restart. Does NOT require new model training but DOES require a code
   change that the merge scope explicitly excluded.

3. **(c) Amend AC-OC-4** to match the actual on-the-wire response
   shape. The current text says response must contain rd2_label and
   rd2_label_status — neither of which exists in the schema today, and
   neither of which can be surfaced without code changes (option b).
   CPO is the AC owner per the spec; this is a CPO scope decision.

## Other ACs

- **AC-OC-1 partial.** Only `rd2_label_status="permanent"` and
  `rd2_label_pinned=true` (nested in `grouped_cv_summary`) are present
  on `main`. `rd2_label_decided_by` and `rd2_label_decision_rule` are
  MISSING. CR review incorrectly reported all 3 sibling fields present
  — diff inspection (commit `09ef6c893`) shows only 1 sibling + 1
  nested.
- **AC-OC-2 ✓.** Cherry-pick integrity verified — 13 files changed
  on main (12 specialist files + 1 metrics annotation). Byte-exact
  match against source `5bf5682ec`.
- **AC-OC-3 ✓.** Zero drift files on main. `git ls-tree -r origin/main`
  shows no `sweep_specialists.py`, no `_sweep/`, no
  `test_sweep_specialists.py`. Case (a) per issue §4.
- **AC-OC-5 ✓.** `ENERGY_PREDICTOR_VERSION="v3.0"` unchanged at
  `apps/api/src/nfm_db/ml/model_version.py:51`.
- **AC-OC-6 ✓.** No dispatcher code added. The merge touches only
  `apps/api/models/specialists/` + `energy_predictor_v3.0_metrics.json`
  + `train_specialists_v40.py` (the latter is a training script, not
  runtime path). No production runtime path modified.

## Disposition

NFM-4042 marked **BLOCKED** with named unblock owners: **CPO** (AC
amendment decision) + **CTO** (architectural ack on whether option (a)
or (b) is permitted under the hard rules). RE cannot resolve without
one of the three options above. Container left running on
`e331da972` (health verified, no production traffic disrupted).

## Captured artifacts

- `/tmp/nfm4042-smoke/predict-energy-puamzr.json` — raw response body
- `docs/verification/NFM-4042-smoke-test-report-2026-09-01.md` — this file