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

Current fixtures (55):

### Original (4)

| Fixture                       | Topic                                              |
| ----------------------------- | -------------------------------------------------- |
| `mox-thermal-conductivity`    | Thermal conductivity of (U,Pu)O₂ MOX fuel          |
| `uo2-fcc-lattice`             | Lattice constant, bulk modulus, melting point of UO₂ |
| `zircaloy-cladding-modulus`   | Elastic moduli and thermal expansion of Zircaloy-4 |
| `thoria-mixed-oxide`          | ThO₂ lattice, elastic, formation-energy properties |

### Multi-page documents (7)

| Fixture                              | Topic                                                |
| ------------------------------------ | ---------------------------------------------------- |
| `multi-page-uo2-irradiation-effects` | Irradiation, fission gas release, restructuring of UO₂ |
| `multi-page-zircaloy-corrosion`     | Oxidation kinetics, hydrogen pickup, mechanical impact |
| `multi-page-sic-coated-particles`    | TRISO SiC coating thermomechanical and irradiation creep |
| `multi-page-fecral-atf`              | FeCrAl ATF oxidation resistance and mechanical props |
| `multi-page-un-nitride-fuel`         | UN advanced fuel thermal and elastic constants |
| `multi-page-beo-reflector`           | BeO reflector thermal conductivity and electronic props |
| `multi-page-stainless-steel-ss316l`  | SS316L primary coolant system irradiation effects |

### Table-containing documents (4)

| Fixture                             | Topic                                      |
| ----------------------------------- | ------------------------------------------ |
| `table-uo2-property-comparison`      | Experimental vs DFT properties of UO₂     |
| `table-mox-composition-property`     | MOX fuel properties vs Pu content           |
| `table-cladding-alloy-comparison`    | Cladding alloy property comparison         |
| `table-nitride-fuel-thermophysical`  | Nitride fuel thermophysical data table      |

### Code-block documents (3)

| Fixture                    | Topic                                         |
| -------------------------- | --------------------------------------------- |
| `code-block-python-extraction` | V2 pipeline Python imports and UO₂ properties |
| `code-block-yaml-config`  | YAML extraction config and UO₂ properties     |
| `code-block-sql-migration` | SQL schema for extraction and Zircaloy-4 props |

### Chinese + English mixed (4)

| Fixture                         | Topic                                       |
| ------------------------------- | ------------------------------------------- |
| `chinese-uo2-thermal-conductivity` | UO₂ thermal conductivity (bilingual)     |
| `chinese-mox-fuel-properties`     | MOX fuel composition and irradiation (bilingual) |
| `chinese-zircaloy-cladding`       | Zircaloy cladding corrosion (bilingual)    |
| `chinese-sic-triso-coating`       | SiC TRISO coating irradiation (bilingual) |

### Diverse materials (15)

| Fixture                              | Topic                                        |
| ------------------------------------ | -------------------------------------------- |
| `u3si2-atf-dispersion-fuel`          | U₃Si₂ ATF dispersion fuel properties         |
| `uo2-poison-fission-products`        | Fission product poisoning in UO₂             |
| `zirconium-diboride-zrb2-coating`    | ZrB₂ ultra-high-temperature ceramic coating   |
| `titanium-diboride-tib2`            | TiB₂ hexagonal AlB2 structure properties     |
| `uranium-carbide-uc-fuel`           | UC monocarbide fuel thermal and mechanical    |
| `magnesia-mgo-insulator`            | MgO nuclear insulator properties              |
| `alumina-al2o3-neutron-insulator`    | α-Al₂O₃ neutron insulator properties        |
| `graphite-moderator-ngr`            | Nuclear graphite moderator irradiation       |
| `puo2-plutonia-properties`           | PuO₂ fluorite structure properties           |
| `nio2-neptunium-dioxide`            | NpO₂ thermophysical and formation energy     |
| `boron-carbide-b4c-control-rod`     | B₄C control rod absorber properties          |
| `zirconium-nitride-zrn-coating`     | ZrN protective coating properties             |
| `yttria-stabilized-zirconia-ysz`    | 8YSZ thermal barrier coating properties      |
| `thorium-carbide-thc-fuel`           | ThC advanced fuel properties                 |
| `americium-oxide-amo2`               | AmO₂ transmutation fuel properties           |

### Diverse properties (7)

| Fixture                              | Topic                                        |
| ------------------------------------ | -------------------------------------------- |
| `creep-rate-zircaloy-high-temp`       | Zircaloy-4 creep at high temperature         |
| `specific-heat-uo2-high-temp`        | UO₂ specific heat at elevated temperature    |
| `thermal-expansion-zircaloy`          | Zircaloy anisotropic thermal expansion        |
| `density-measurement-porosity-uo2`    | UO₂ pellet porosity and thermal conductivity |
| `elastic-moduli-thoria-high-pressure` | ThO₂ elastic moduli under high pressure       |
| `band-gap-nuclear-materials`          | Band gaps across nuclear fuel materials       |
| `formation-energy-actinide-oxides`    | Formation energy of UO₂, ThO₂, PuO₂          |

### Edge cases (10)

| Fixture                     | Topic                                  |
| --------------------------- | -------------------------------------- |
| `empty-sections-document`   | Document with intentionally empty sections |
| `no-sections-single-block` | Single-block text with no headings    |
| `deeply-nested-markdown`   | H1 to H2 nested heading structure      |
| `unicode-special-chars`    | Unicode characters in measurements     |
| `many-formulas-single-section` | Compound identification with many formulas |
| `duplicate-properties-repeated` | Repeated property mentions in text   |
| `h3-h4-nested-headings`    | H1 to H4 heading hierarchy  |
| `multiline-measurement-values` | Multi-line measurement data for ThO₂   |

### Additional structural alloys (3)

| Fixture                     | Topic                                  |
| --------------------------- | -------------------------------------- |
| `inconel-718-spacer-grid`  | Inconel 718 spacer grid properties     |
| `hafnium-diboride-hfb2-atf` | HfB₂ ATF ceramic properties            |
| `ag-in-cd-control-rod`     | Ag-In-Cd control rod alloy properties   |

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