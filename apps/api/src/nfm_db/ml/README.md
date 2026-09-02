# `nfm_db.ml` — model cards and fail-closed gates

Research code for the NFMD intelligent design engine (技术路线图 §5). Training
entrypoints write a model artifact plus a metrics JSON ("model card") to
`models/`; the card is the reviewable record of a run, not a log.

## PhaseClassifier v2.0 — `train_v20.py`

| Artifact | Path |
|---|---|
| Model | `models/phase_classifier_v2.0.joblib` |
| Model card | `models/phase_classifier_v2.0_metrics.json` |

Locked protocol: 8 physical features, `GroupKFold` by element system, 5 splits,
seed 42. Grouping by element system is what prevents near-duplicate compositions
from spanning folds (NFM-1753 / NFM-1756).

### Acceptance is macro-F1 + M-recall, not accuracy

The roadmap's original accuracy bars (>75% Sprint 4, >80% Sprint 6) were set
without a baseline check. On the 2951:784 H/M split, predicting `H` for every
composition scores 0.7901 accuracy — a constant predictor passes Sprint 4
outright. The NDE ruling on NFM-3954 re-anchored acceptance to a dual criterion
recorded in the card's `acceptance_criteria` block:

| Stage | macro-F1 ≥ | M-recall ≥ |
|---|---|---|
| Sprint 4 | 0.65 | 0.25 |
| Sprint 5 | 0.70 | 0.40 |
| Sprint 6 | 0.75 | 0.60 |
| Final | 0.80 | 0.70 |

### `dummy_baseline` — the fail-closed floor

So the bar can never drift past a constant predictor again, every run scores
`DummyClassifier(strategy='most_frequent')` on the **same** GroupKFold splits as
the model and writes the comparison to the card (NFM-3957):

```json
"dummy_baseline": {
  "strategy": "most_frequent",
  "macro_f1_mean": 0.438901,
  "macro_f1_std": 0.037696,
  "macro_f1_per_fold": [0.393989, 0.472045, 0.39187, 0.473239, 0.463362],
  "n_splits": 5,
  "lift_over_dummy_pp": 23.81,
  "paired_with_model_splits": true
}
```

The splits are materialized once by `locked_group_kfold_splits()` and handed to
both scorers, so the comparison is paired fold-for-fold rather than against an
equivalently configured splitter.

**`lift_over_dummy_pp < 0` is a fail-closed trigger.** The model scored below a
constant predictor, so `_apply_dummy_baseline_gate()` re-labels the run
`[EXPLORATORY]` and sets `rd3_triggered`, alongside the existing RD-2 accuracy
range and `mixing_enthalpy` importance gates. A run in that state is not
publishable as a result: open an RD-3 anomaly review before touching the model.

Read `lift_over_dummy_pp` before the headline metric. A card whose accuracy
looks healthy but whose lift is near zero is a card describing the class
distribution, not the model.

## Related

- `group_kfold_cv.py` — element-system grouping and per-fold reporting.
- `prediction_service.py` — surfaces `per_class_recall` and the active
  acceptance criterion on every prediction envelope.
- `train_energy_v30_grouped_cv.py` — the regression counterpart; the
  `DummyRegressor` analog of this gate is tracked under NFM-3953.
