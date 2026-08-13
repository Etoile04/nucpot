# V2 Parity Baseline

This directory hosts the **reference baseline** parity test for the V2
extraction pipeline. It compares V2's output against curated,
human-validated ``expected.json`` snapshots — not against V1 output.

## Why a reference baseline instead of V1?

The previous parity check ran V2 against V1 live LLM output. On staging
that comparison is meaningless:

1. Staging has **no LLM keys** configured (per NFM-2890 / NFM-2869).
2. V1 stub mode returns canned data, so V2-vs-stub output measures
   stub drift, not V2 quality.
3. The LLM-on-V1 path is also non-deterministic, so the comparison
   produces flaky failures unrelated to V2 regressions.

The reference baseline approach (NFM-2890, Option 2 chosen by CTO) is:

- **Deterministic** — re-running the test on the same input yields
  byte-identical output.
- **Auditable** — each baseline is checked into the repo and tied to
  a human-validated source paper.
- **Architecturally superior** — the test measures V2 against ground
  truth, not against another model's behaviour.

## Layout

```
tests/parity/
├── README.md              # this file
├── conftest.py            # sys.path bootstrap for V2 imports
└── test_parity.py         # the parametrized parity test

tests/fixtures/parity/baseline/<fixture-name>/
├── source.txt             # raw input paper excerpt
└── expected.json          # curated expected V2 output

tools/parity_baseline/
└── compute_expected.py    # regenerates expected.json from source.txt
                          # by mirroring the V2 step implementations
```

Current fixtures (4 — within the 3-5 target):

| Fixture                       | Topic                                              |
| ----------------------------- | -------------------------------------------------- |
| `mox-thermal-conductivity`    | Thermal conductivity of (U,Pu)O₂ MOX fuel          |
| `uo2-fcc-lattice`             | Lattice constant, bulk modulus, melting point of UO₂ |
| `zircaloy-cladding-modulus`   | Elastic moduli and thermal expansion of Zircaloy-4 |
| `thoria-mixed-oxide`          | ThO₂ lattice, elastic, formation-energy properties |

## What the test does

For each fixture:

1. Read ``source.txt`` and the curated ``expected.json``.
2. Drive the 5 V2 steps (RawTextLoader → SectionSegmenter →
   EntityExtractor → PropertyNormalizer → ChunkBuilder) over
   ``source.txt`` directly. The test does **not** instantiate the V2
   orchestrator because that class requires an AsyncSession and a
   persisted ``ExtractionJob`` UUID — both irrelevant to the unit
   comparison.
3. Convert the resulting ``ExtractionChunk`` instances to JSON-shaped
   dicts (matching the wire format the orchestrator emits).
4. Assert structural equality against ``expected.json``.

On divergence, the test fails with a structured field-level diff that
names which chunk field drifted. The test report also reminds the
reader how to regenerate the baseline if the change is intentional.

## What this test does NOT catch

- **V1 vs V2 divergence where both are correct.** A common V1 bug
  shape is "V1 silently dropped a measurement"; if the baseline
  captures the same drop because V2 also drops it, both engines
  produce equal-but-wrong output. The reference baseline protects
  against V2 regressions **from the human-validated state**, not
  against systemic errors that pre-date the baseline.
- **Long-running or networked LLM calls.** The test runs entirely in
  stub / local mode — no API keys required.
- **Ontology-version drift.** Property aliases and unit
  normalisations are baked into the V2 step modules; when the
  ontology evolves, regenerating the baselines (see below) is
  intentional and expected.

## Updating a baseline

When V2 legitimately improves (e.g. a new property alias added), the
parity test will fail and require a baseline refresh. The procedure:

1. Confirm the V2 change is intentional and human-validated against
   the source paper.
2. Regenerate the affected baseline:

   ```bash
   python tools/parity_baseline/compute_expected.py \
       tests/fixtures/parity/baseline/<fixture>/source.txt \
       tests/fixtures/parity/baseline/<fixture>/expected.json
   ```

3. Inspect the diff (``git diff tests/fixtures/parity/baseline/...``).
   Each ``expected.json`` carries a ``summary`` block with
   formula/property/measurement counts; eyeball those for sanity.
4. Commit the regenerated baseline alongside the V2 step change in the
   same PR. Reviewers must be able to see both the code change and
   the baseline regeneration in one review.

## Adding a new fixture

1. Choose a short kebab-case name describing the topic.
2. Create ``tests/fixtures/parity/baseline/<name>/source.txt`` with a
   short, self-contained excerpt from a nuclear-fuel literature paper
   that exercises the V2 regex patterns (formulas, property names,
   measurements).
3. Generate the initial baseline:

   ```bash
   python tools/parity_baseline/compute_expected.py \
       tests/fixtures/parity/baseline/<name>/source.txt \
       tests/fixtures/parity/baseline/<name>/expected.json
   ```

4. **Manually validate** the generated ``expected.json`` against the
   source paper — read every ``metadata.entities.formulas`` /
   ``properties`` / ``measurements`` entry and confirm it corresponds
   to a phrase in ``source.txt``. The regeneration script mirrors V2
   byte-for-byte, so any V2 bug becomes a baked-in baseline bug
   unless human review catches it.
5. Run the parity test: ``pytest -m parity tests/parity/`` and confirm
   it passes.

## Running

```bash
# Run only parity tests:
pytest -m parity tests/parity/

# Run a single fixture:
pytest -m parity tests/parity/ -k uo2-fcc-lattice

# Run with verbose diff output on failure:
pytest -m parity tests/parity/ -v
```

The parity marker is registered in the root ``pyproject.toml`` under
``[tool.pytest.ini_options] markers``.