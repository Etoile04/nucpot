# Extraction `conditions` Schema (NFM-1979 / AC-4)

This document is the canonical reference for the `conditions` field on
`nfm_db.schemas.extraction.ExtractedProperty`. It specifies the five
**standard keys** that the OntoFuel → NucPot ingestion pipeline recognizes,
their units, and how unknown keys are handled.

## Background

`ExtractedProperty.conditions` is an open-ended `dict[str, Any] | None`
capturing the experimental conditions under which a property value was
measured (temperature, pressure, irradiation, etc.). The schema accepts any
key, but the mapper recognizes a small fixed set and stores them in
`MeasurementCondition` columns; anything else is preserved in `notes`.

This contract was finalized in **NFM-1979 / AC-4** alongside the
`property_category` literal tightening.

## Standard Keys

| Key                | Type            | Unit           | DB column            | Notes                                                           |
| ------------------ | --------------- | -------------- | -------------------- | --------------------------------------------------------------- |
| `temperature`      | `float \| int`  | K (Kelvin)     | `temperature`        | Direct copy. Alias `temp` is also accepted.                     |
| `pressure`         | `float \| int`  | Pa (Pascal)    | `pressure`           | Direct copy. Negative values allowed (e.g., vacuum ≈ 0).        |
| `neutron_flux`     | `float`         | n/cm²·s        | `notes` (no column)  | Not stored in a dedicated column; preserved as `"neutron_flux=<value>"` in `MeasurementCondition.notes`. |
| `dose`             | `float`         | dpa (displacements per atom) | `irradiation_dose` | Mapped to `irradiation_dose`. Alias `irradiation_dose` is also accepted. |
| `strain_rate`      | `float`         | 1/s            | `notes` (no column)  | Not stored in a dedicated column; preserved as `"strain_rate=<value>"` in `MeasurementCondition.notes`. |

## How Unknown Keys Are Handled

The mapper iterates `conditions` in insertion order:

1. **Known keys** (`temperature`, `pressure`, `dose`/`irradiation_dose`,
   `environment`, plus the no-column-but-preserved keys `neutron_flux`,
   `strain_rate`) are mapped to the corresponding field.
2. **Unknown keys** are appended to `notes` in the form
   `"<key>=<value>"`, separated by `; `.
3. If the user supplied an explicit `notes` string in the conditions dict,
   it is preserved as the first segment of the final `notes` value.

Examples:

```python
# Standard 5 keys — temperature & pressure hit columns; dose → irradiation_dose;
# neutron_flux and strain_rate flow into notes.
conditions={
    "temperature": 600,
    "pressure": 0.1,
    "neutron_flux": 1.0e18,
    "dose": 5.0,
    "strain_rate": 1.0e-6,
}
# → MeasurementCondition(
#     temperature=600, pressure=0.1, irradiation_dose=5.0,
#     notes="neutron_flux=1e+18; strain_rate=1e-06",
# )

# Unknown key — preserved in notes.
conditions={"temperature": 300, "humidity": 0.65}
# → MeasurementCondition(temperature=300, notes="humidity=0.65")
```

## Migration Guidance

If you have a legacy extraction record using a non-standard key that you
want indexed separately:

1. Add a new column to `MeasurementCondition` via Alembic migration.
2. Extend `_CONDITION_KEY_MAP` in
   `nfm_db/services/extraction_to_db_mapper.py` with the new mapping.
3. Add the new key to the table above.
4. Update `_build_condition_kwargs` and add a unit test in
   `tests/services/test_extraction_to_db_mapper.py::TestConditionsStandardKeysRoundTrip`.

For one-off measurements where a dedicated column is overkill, leave the
key in `notes` — the round-trip preserves it losslessly.

## Validation Behavior

`ExtractedProperty.conditions` is `dict[str, Any] | None`. Pydantic does
**not** restrict the key set or coerce values; the mapper is responsible
for the mapping. This is intentional: unknown future keys flow through
the `notes` escape hatch with zero data loss.

If you need stricter validation for a downstream consumer, derive a
sub-schema from this table rather than modifying the upstream schema.

## Tests

The contract is locked in by:

- `tests/test_extraction_schema.py::test_conditions_*` — schema-level
  acceptance of each standard key.
- `tests/services/test_extraction_to_db_mapper.py::TestConditionsStandardKeysRoundTrip`
  — end-to-end round-trip into `MeasurementCondition`.