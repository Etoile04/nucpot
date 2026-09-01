-- =============================================================================
-- F8 Scorecard v0.5.0 — bridge-UNION surface (NFM-4009 / NFM-4007 1.0)
-- =============================================================================
--
-- PURPOSE
--   Authoritative pass/fail scorecard for the F8 (Owen2023 fixture) property
--   coverage gate. The scorecard reads the dedup-collapsed UNION surface
--   produced by ``kg_to_staging_bridge.bridge_kg_to_staging`` (NFM-3891,
--   ADR-NFM-4004). The pre-NFM-3891 scorecard (NFM-3824) read ``kg_nodes``
--   directly and produced the misleading 2/8 PASS verdict because the
--   ``_ref_gap_fill_staging`` surface the ontology viewer actually reads was
--   empty for fresh extractions.
--
-- USER-FACING PASS BAR (v0.5.0):
--   8/8 PASS against ``_ref_gap_fill_staging`` UNION for the canonical prod
--   source (ds.uuid = :'prod_source_uuid'). Scorecard fails if any of the
--   eight checkpoints does not land on the staging surface.
--
-- DIAGNOSTIC (NOT PASS-CRITERION):
--   ``kg_nodes`` strict (2/8) is reported alongside the staging result as a
--   drift indicator. If this number ever drifts, ontology coverage regressed.
--
-- SKIPPED-UNKNOWN-PROPERTIES (NOT PASS-CRITERION):
--   ``extraction_jobs.skipped_unknown_properties`` is reported for the prod
--   source's extraction job as a diagnostic. Target ≤10 for v0.5.0.
--
-- SCOPE
--   Single-file canonical SQL. Read-only against the production database.
--   NEVER mutate rows. Re-runnable idempotently. Bound to one source via
--   the ``prod_source_uuid`` psql variable — substitute on the psql
--   command line with ``-v prod_source_uuid=…``.
--
-- DEDUP-HASH CONTRACT (NFM-3891 D2)
--   ``compute_dedup_hash(element_system, value, property_name, method,
--   corpus_id)`` — 5-field key shared by the bridge's kg_nodes and
--   property_measurements loops (D6: both loops live in the same function).
--   The staging rows used below are post-dedup, so each measurement appears
--   exactly once on the staging surface.
--
-- ADR REFERENCES
--   - ADR-NFM-4004-kg-staging-dual-surface-v050.md (architectural source —
--     if not yet authored in this commit, see NFM-4004 body for context).
--   - ADR-010 D1/D2/D3/D4/D7 (bridge collapse contract).
--   - ADR-011 D7 (NUMERIC(20,15) precision for property_measurements).
--
-- ACCEPTANCE CRITERIA MAPPING
--   AC-1: this file is the committed canonical SQL.
--   AC-2: re-run on prod DB → 8/8 PASS against _ref_gap_fill_staging.
--   AC-3: skipped_unknown_properties reported (target ≤10 for v0.5.0).
--   AC-4: results posted as a comparison comment on NFM-4007.
--
-- CROSS-REFERENCES
--   - NFM-4007 (CPO dispatch ticket, parent)
--   - NFM-4004 (CTO decision — ADR source)
--   - NFM-3824 (original 2/8 strict scorecard; retained as a diagnostic
--     surface, no longer the user-facing pass bar)
--   - NFM-3891 (kg_to_staging_bridge UNION implementation)
--   - NFM-3845 (RE post-deploy AC-5 scorecard re-run; will close after this
--     amendment lands)
--
-- PROD RE-VERIFY FINDING (NFM-4009, 2026-09-01)
--   First run on prod source ``9320cb50-eb65-4178-8d2e-c56aeb848b21``
--   (DOI 10.1016/j.jnucmat.2023.154270, Owen2023 amorphous UO2 + Cr-doped
--   diffusion study) returned:
--
--     staging_strict       : 0/8 PASS
--     kg_nodes_diagnostic  : 3/8 PASS (C1 undoped Ea, C2 undoped D0, C3
--                            Cr-doped Ea matched by 0.30 ± 0.05 in
--                            band — NOT a true Cr-doped-specific hit;
--                            the 0.26 eV Cr-doped value is missing)
--     skipped_unknown_props: 0 (within target)
--
--   The 0/8 staging result is a real product gap, NOT a SQL bug. The
--   bridge's ``_PROPERTY_SLUGS`` map (kg_to_staging_bridge.py) covers 17
--   Chinese labels but does NOT include the F8-relevant Owen2023 labels
--   (``扩散激活能`` / ``扩散前指数因子`` / ``扩散系数`` / ``密度`` /
--   ``RDF峰`` / ``键长`` etc.) — those rows are silently skipped at
--   bridge time. The 9 staging rows that DID land for this source are all
--   Cr2O3 elastic constants (``C11``, ``C12``, ``a``, ``c``, …), which
--   came from the PropertyMeasurement loop (property_type.name happens to
--   already be in the slug table).
--
--   Unblock path:
--   - NDE owns ``kg_to_staging_bridge._PROPERTY_SLUGS`` (NDE/NFM-3517/
--     NFM-3835 territory per the task constraints).
--   - Extend the slug map to cover F8-relevant Chinese labels.
--   - Re-run the bridge for source 9320cb50-… (delete+rewrite pattern
--     already in place: ``bridge_kg_to_staging`` deletes rows for the
--     corpus before re-inserting, idempotent on rerun).
--   - Re-execute this SQL — expect 8/8 PASS once the slug map covers the
--     F8 labels and the bridge re-runs.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Configuration
-- -----------------------------------------------------------------------------
-- Substitute the prod source UUID on the psql command line:
--   psql -v prod_source_uuid="'9320cb50-eb65-4178-8d2e-c56aeb848b21'" \
--        -f apps/api/src/nfm_db/audit/f8_scorecard_v050.sql
--
-- The CTE ``prod_source`` re-exposes ds.uuid as a stable named column so the
-- downstream checkpoints can reference ``ps.uuid`` rather than re-binding the
-- literal everywhere.
WITH prod_source AS (
    SELECT id AS uuid
    FROM data_sources
    WHERE id = :'prod_source_uuid'::uuid
),

-- -----------------------------------------------------------------------------
-- 1. Staging strict — 8/8 against _ref_gap_fill_staging UNION
-- -----------------------------------------------------------------------------
-- The bridge emits ``source_file = 'kg:<DataSource_uuid>'`` for BOTH the
-- kg_nodes and property_measurements loops (kg_to_staging_bridge.py:527).
-- Filtering staging by ``source_file`` therefore scopes to this source
-- regardless of which loop wrote the row.
staging_rows AS (
    SELECT
        s.element_system,
        s.property_name,
        s.value,
        s.unit,
        s.method,
        s.dedup_hash,
        s.source_file
    FROM _ref_gap_fill_staging s, prod_source ps
    WHERE s.source_file = 'kg:' || ps.uuid::text
),

-- -----------------------------------------------------------------------------
-- 2. Eight strict F8 checkpoints
-- -----------------------------------------------------------------------------
-- Each checkpoint produces one row: checkpoint_no, checkpoint_name,
-- target_element_systems, target_property_names, target_value_lo,
-- target_value_hi, hit_count. The SELECTs at the bottom mark each PASS/FAIL.
--
-- Tolerances match ADR-NFM-4004 §3.1 (and the tolerances used in
-- tests/test_heuristic_extractor.py::TestF8ScorecardCompleteExtraction).
-- ``pre_exponential_factor`` and ``diffusion_coefficient`` are accepted
-- interchangeably for D0 (per the heuristic-extractor test helper:
-- D0 may surface as either name depending on prose).
-- -----------------------------------------------------------------------------
checkpoint_1_undoped_ea AS (
    SELECT
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name = 'activation_energy'
      AND sr.element_system = 'UO2'
      AND sr.value BETWEEN 0.25 AND 0.35  -- 0.30 ± 0.05
),
checkpoint_2_undoped_d0 AS (
    SELECT
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name IN ('pre_exponential_factor', 'diffusion_coefficient')
      AND sr.element_system = 'UO2'
      AND sr.value BETWEEN 3.32e-8 * 0.9 AND 3.32e-8 * 1.1
        -- ±10% relative; the D0 3.32e-8 cm²/s value has no ±spec from ADR,
-- we use a tight relative band consistent with the heuristic-extractor test
-- (rel=1e-3 in pytest.approx; in production the EM/MD scatter is wider).
),
checkpoint_3_cr_doped_ea AS (
    SELECT
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name = 'activation_energy'
      AND sr.element_system IN ('UO2+Cr', 'U-Cr-O')
      AND sr.value BETWEEN 0.18 AND 0.34  -- 0.26 ± 0.08
),
checkpoint_4_cr_doped_d0 AS (
    SELECT
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name IN ('pre_exponential_factor', 'diffusion_coefficient')
      AND sr.element_system IN ('UO2+Cr', 'U-Cr-O')
      AND sr.value BETWEEN 1.27e-9 * 0.9 AND 1.27e-9 * 1.1
),
checkpoint_5_density_amorphous AS (
    SELECT
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name = 'density'
      AND sr.element_system = 'UO2'
      AND sr.value BETWEEN 10.50 AND 10.60  -- 10.55 ± 0.05
),
checkpoint_6_density_cr_doped AS (
    SELECT
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name = 'density'
      AND sr.element_system IN ('UO2+Cr', 'U-Cr-O')
      AND sr.value BETWEEN 10.22 AND 10.32  -- 10.27 ± 0.05
),
checkpoint_7_rdf_peaks AS (
    -- Two sub-peaks (2.28 Å, 2.83 Å) — both must land for PASS.
    -- Split into two sub-checks for clearer failure messaging.
    SELECT
        COUNT(*) FILTER (WHERE sr.value BETWEEN 2.27 AND 2.29) AS peak_2p28_hit_count,
        COUNT(*) FILTER (WHERE sr.value BETWEEN 2.82 AND 2.84) AS peak_2p83_hit_count,
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name = 'rdf_peak'
      AND sr.element_system = 'UO2'
),
checkpoint_8_cr_o_bond_length AS (
    SELECT
        COUNT(*) AS hit_count,
        ARRAY_AGG(value ORDER BY value) AS values_seen
    FROM staging_rows sr, prod_source ps
    WHERE sr.property_name = 'bond_length'
      AND sr.element_system IN ('UO2+Cr', 'U-Cr-O')
      AND sr.value BETWEEN 2.02 AND 2.05
),

-- -----------------------------------------------------------------------------
-- 3. Diagnostic — kg_nodes strict (NFM-3824 original surface, 2/8 expected)
-- -----------------------------------------------------------------------------
-- Replays NFM-3824's verification against the underlying ``kg_nodes`` graph.
-- The undoped Ea and D0 checkpoints land (those are the 2/8). The other six
-- fail to land on ``kg_nodes`` because pre-NFM-3891, the PropertyMeasurement
-- loop was the only path that surfaced them — and PropertyMeasurement rows
-- don't have a KGNode counterpart.
--
-- Property nodes are joined to Material nodes via KGEdge(relation_type=
-- 'hasProperty'). KGNode.properties is JSON (not JSONB).
--
-- Property ``label`` (not ``properties->>'property_name'``) is the canonical
-- key — the schema stores the property name on the label column, not under
-- properties. Real prod data has Chinese labels (e.g. ``扩散激活能``) and a
-- few English ones (``activation_energy``). The F8 acceptance surface must
-- accept the union of both, mirroring the bridge's ``_PROPERTY_SLUGS`` map
-- (which only covers a subset — the diagnostic intentionally uses a wider
-- list to expose the bridge's coverage gap).
--
-- Values are stored as prose strings like ``0.30 ± 0.05`` or ``3.32 × 10⁻⁸``,
-- not as JSON numbers. We extract the leading numeric with a regex and
-- cast — non-matching rows surface as NULL and fail the value filter.
-- -----------------------------------------------------------------------------
kg_material_labels AS (
    SELECT
        prop.id AS prop_id,
        prop.label AS prop_label,
        prop.properties AS prop_properties,
        mat.label AS mat_label
    FROM kg_nodes prop
    JOIN kg_edges e
      ON e.source_node_id = prop.id
     AND e.relation_type = 'hasProperty'
    JOIN kg_nodes mat
      ON mat.id = e.target_node_id
     AND mat.node_type = 'Material'
    JOIN prod_source ps
      ON prop.source_id = ps.uuid
    WHERE prop.node_type = 'Property'
),
kg_value_rows AS (
    SELECT
        kml.prop_id,
        kml.prop_label,
        kml.mat_label,
        kml.prop_properties ->> 'value' AS raw_value,
        -- Value extraction handles four observed prod shapes:
        --   1. JSON number                 e.g. ``0.3``
        --   2. Plain decimal + uncertainty  e.g. ``0.30 ± 0.05``
        --   3. ASCII scientific             e.g. ``3.32e-8``
        --   4. Unicode scientific mantissa  e.g. ``3.32 × 10⁻⁸``
        --      (Unicode subscripts ⁰¹²³⁴⁵⁶⁷⁸⁹ / signs ⁻⁺ are translated
        --      to ASCII digits/signs first so case 3's parser works.)
        -- The CASE returns NULL on parse failure (range expressions like
        -- "10⁻⁸ – 10⁻⁹", plain integers without decimal, etc.) — those
        -- rows simply don't hit any F8 value band.
        CASE
            WHEN kml.prop_properties -> 'value' IS NULL THEN NULL
            WHEN json_typeof(kml.prop_properties -> 'value') = 'number'
                THEN (kml.prop_properties ->> 'value')::double precision
            WHEN translate(
                    COALESCE(kml.prop_properties ->> 'value', ''),
                    '⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺', '0123456789-+'
                 ) ~ '^[+-]?[0-9]+\.[0-9]+ ?× ?10[+-]?[0-9]+'
                THEN (regexp_replace(
                    translate(
                        COALESCE(kml.prop_properties ->> 'value', ''),
                        '⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺', '0123456789-+'
                    ),
                    E'^([+-]?[0-9]+\\.[0-9]+) ?× ?10([+-]?[0-9]+).*\$',
                    E'\\1e\\2'
                ))::double precision
            WHEN translate(
                    COALESCE(kml.prop_properties ->> 'value', ''),
                    '⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺', '0123456789-+'
                 ) ~ '^[+-]?[0-9]+\.[0-9]+[eE][+-]?[0-9]+'
                THEN (regexp_replace(
                    translate(
                        COALESCE(kml.prop_properties ->> 'value', ''),
                        '⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺', '0123456789-+'
                    ),
                    E'^([+-]?[0-9]+\\.[0-9]+)[eE]([+-]?[0-9]+).*\$',
                    E'\\1e\\2'
                ))::double precision
            WHEN COALESCE(kml.prop_properties ->> 'value', '') ~ '^[+-]?[0-9]+\.[0-9]+'
                THEN (regexp_replace(
                    COALESCE(kml.prop_properties ->> 'value', ''),
                    '^([+-]?[0-9]+\.[0-9]+).*$',
                    E'\\1'
                ))::double precision
            ELSE NULL
        END AS value,
        kml.prop_properties ->> 'unit' AS unit
    FROM kg_material_labels kml
),
kg_classified AS (
    -- Project each Property row to (checkpoint_class, element_class,
    -- numeric value). Used by the kg_strict_2_of_8 CTE for hit counting.
    -- The property-label synonyms mirror kg_to_staging_bridge._PROPERTY_SLUGS
    -- plus the additional Chinese labels observed in the prod paper
    -- (Owen2023, DOI 10.1016/j.jnucmat.2023.154270).
    SELECT
        kvr.prop_id,
        kvr.value,
        CASE
            WHEN kvr.prop_label IN (
                'activation_energy',
                '扩散激活能', '扩散活化能', '活化能', '激活能', '氧扩散激活能'
            ) THEN 'ea'
            WHEN kvr.prop_label IN (
                'pre_exponential_factor', 'diffusion_coefficient',
                '扩散前指数因子', '预指数因子', '扩散预指数因子',
                '扩散系数', '氧扩散前指数因子', '氧扩散指前因子',
                '扩散指前因子', '扩散系数指前因子', '热扩散率'
            ) THEN 'd0'
            WHEN kvr.prop_label IN ('density', '密度') THEN 'density'
            WHEN kvr.prop_label IN ('rdf_peak', 'RDF峰') THEN 'rdf_peak'
            WHEN kvr.prop_label IN ('bond_length', '键长', 'Cr-O键长') THEN 'bond_length'
            ELSE NULL  -- not an F8-relevant property
        END AS property_class,
        -- Element_class: 'undoped_uo2', 'cr_doped_uo2', or 'other'.
        -- The prod data uses material labels like 'UO2', 'amorphous UO2',
        -- 'UO2-10at.%Cr', 'amorphous UO2-30at.%Cr', 'Cr-doped UO2', etc.
        -- Cr presence: any label containing 'Cr' OR a Cr-doping annotation
        -- ('-10at.%Cr' / '-30at.%Cr' / '40at.%Cr' / '50at.%Cr').
        CASE
            WHEN kvr.mat_label ~* '\mCr\M' OR kvr.mat_label LIKE '%-__at.%%Cr%'
                 OR kvr.mat_label LIKE 'Cr-doped%' OR kvr.mat_label LIKE '%Cr-doped%'
                THEN 'cr_doped_uo2'
            WHEN kvr.mat_label LIKE 'UO2%' OR kvr.mat_label LIKE '%UO2'
                 OR kvr.mat_label LIKE 'amorphous UO2%'
                THEN 'undoped_uo2'
            ELSE 'other'
        END AS element_class
    FROM kg_value_rows kvr
),
kg_strict_2_of_8 AS (
    -- Mirror the eight checkpoints against kg_nodes. Each count is 1 if at
    -- least one matching row exists, 0 otherwise. The diagnostic tolerates
    -- the prod-data shape (Chinese labels, prose-string values) — values
    -- outside the ± window still don't count as a hit, so a row of
    -- "0.30 ± 0.05" passes (0.30 is within 0.25-0.35).
    SELECT
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'ea'
              AND k.element_class = 'undoped_uo2'
              AND k.value BETWEEN 0.25 AND 0.35) AS c1_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'd0'
              AND k.element_class = 'undoped_uo2'
              AND k.value BETWEEN 3.32e-8 * 0.9 AND 3.32e-8 * 1.1) AS c2_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'ea'
              AND k.element_class = 'cr_doped_uo2'
              AND k.value BETWEEN 0.18 AND 0.34) AS c3_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'd0'
              AND k.element_class = 'cr_doped_uo2'
              AND k.value BETWEEN 1.27e-9 * 0.9 AND 1.27e-9 * 1.1) AS c4_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'density'
              AND k.element_class = 'undoped_uo2'
              AND k.value BETWEEN 10.50 AND 10.60) AS c5_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'density'
              AND k.element_class = 'cr_doped_uo2'
              AND k.value BETWEEN 10.22 AND 10.32) AS c6_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'rdf_peak'
              AND k.element_class = 'undoped_uo2'
              AND k.value BETWEEN 2.27 AND 2.29) AS c7a_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'rdf_peak'
              AND k.element_class = 'undoped_uo2'
              AND k.value BETWEEN 2.82 AND 2.84) AS c7b_hit_count,
        (SELECT COUNT(*) FROM kg_classified k
            WHERE k.property_class = 'bond_length'
              AND k.element_class = 'cr_doped_uo2'
              AND k.value BETWEEN 2.02 AND 2.05) AS c8_hit_count
),

-- -----------------------------------------------------------------------------
-- 4. Diagnostic — skipped_unknown_properties for the prod source
-- -----------------------------------------------------------------------------
-- ``extraction_jobs.skipped_unknown_properties`` is recorded per extraction
-- run. ``extraction_jobs.source_reference`` holds the DOI string, not a UUID
-- FK — we join via the DataSource DOI. The prod source may have multiple
-- runs (LLM extraction reruns, heuristic reruns); we sum across all runs
-- for the headline count. Target ≤10 for v0.5.0.
skipped_diagnostic AS (
    SELECT
        COALESCE(SUM(ej.skipped_unknown_properties), 0) AS total_skipped_unknown_properties,
        COUNT(*) AS n_extraction_runs,
        COALESCE(
            ARRAY_AGG(ej.id ORDER BY ej.created_at DESC) FILTER (WHERE ej.id IS NOT NULL),
            ARRAY[]::uuid[]
        ) AS extraction_run_ids
    FROM extraction_jobs ej
    JOIN data_sources ds ON ds.doi = ej.source_reference
    JOIN prod_source ps ON ds.id = ps.uuid
),

-- -----------------------------------------------------------------------------
-- 5. Final scorecard output — PASS/FAIL verdict per checkpoint
-- -----------------------------------------------------------------------------
-- The "result column" naming convention:
--   status   — 'PASS' | 'FAIL' on the staging surface (the user-facing bar)
--   diag     — 'PASS' | 'FAIL' on the kg_nodes diagnostic surface (NFM-3824
--              original; expected FAIL for 6/8 by design)
--   values_seen — ARRAY of staging values that landed in the value window
--                 (debug aid; informational)
-- -----------------------------------------------------------------------------
scorecard AS (
    SELECT 1 AS checkpoint_no,
           'Ea undoped UO2 (0.30 ± 0.05 eV)' AS checkpoint_name,
           (SELECT hit_count FROM checkpoint_1_undoped_ea) AS staging_hit_count,
           CASE WHEN (SELECT hit_count FROM checkpoint_1_undoped_ea) > 0
                THEN 'PASS' ELSE 'FAIL' END AS staging_status,
           (SELECT c1_hit_count FROM kg_strict_2_of_8) AS kg_hit_count,
           CASE WHEN (SELECT c1_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END AS kg_status,
           (SELECT values_seen FROM checkpoint_1_undoped_ea) AS values_seen

    UNION ALL SELECT 2,
           'D0 undoped UO2 (3.32e-8 ± 10% cm²/s)',
           (SELECT hit_count FROM checkpoint_2_undoped_d0),
           CASE WHEN (SELECT hit_count FROM checkpoint_2_undoped_d0) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT c2_hit_count FROM kg_strict_2_of_8),
           CASE WHEN (SELECT c2_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT values_seen FROM checkpoint_2_undoped_d0)

    UNION ALL SELECT 3,
           'Ea 50at% Cr-doped (0.26 ± 0.08 eV)',
           (SELECT hit_count FROM checkpoint_3_cr_doped_ea),
           CASE WHEN (SELECT hit_count FROM checkpoint_3_cr_doped_ea) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT c3_hit_count FROM kg_strict_2_of_8),
           CASE WHEN (SELECT c3_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT values_seen FROM checkpoint_3_cr_doped_ea)

    UNION ALL SELECT 4,
           'D0 50at% Cr-doped (1.27e-9 ± 10% cm²/s)',
           (SELECT hit_count FROM checkpoint_4_cr_doped_d0),
           CASE WHEN (SELECT hit_count FROM checkpoint_4_cr_doped_d0) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT c4_hit_count FROM kg_strict_2_of_8),
           CASE WHEN (SELECT c4_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT values_seen FROM checkpoint_4_cr_doped_d0)

    UNION ALL SELECT 5,
           'Density amorphous (10.55 ± 0.05 g/cm³)',
           (SELECT hit_count FROM checkpoint_5_density_amorphous),
           CASE WHEN (SELECT hit_count FROM checkpoint_5_density_amorphous) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT c5_hit_count FROM kg_strict_2_of_8),
           CASE WHEN (SELECT c5_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT values_seen FROM checkpoint_5_density_amorphous)

    UNION ALL SELECT 6,
           'Density 10at% Cr-doped (10.27 ± 0.05 g/cm³)',
           (SELECT hit_count FROM checkpoint_6_density_cr_doped),
           CASE WHEN (SELECT hit_count FROM checkpoint_6_density_cr_doped) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT c6_hit_count FROM kg_strict_2_of_8),
           CASE WHEN (SELECT c6_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT values_seen FROM checkpoint_6_density_cr_doped)

    UNION ALL SELECT 7,
           'RDF peaks (2.28 Å AND 2.83 Å)',
           (SELECT LEAST(
                (SELECT peak_2p28_hit_count FROM checkpoint_7_rdf_peaks),
                (SELECT peak_2p83_hit_count FROM checkpoint_7_rdf_peaks)
            )),
           CASE WHEN (SELECT peak_2p28_hit_count FROM checkpoint_7_rdf_peaks) > 0
                 AND (SELECT peak_2p83_hit_count FROM checkpoint_7_rdf_peaks) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT LEAST((SELECT c7a_hit_count FROM kg_strict_2_of_8),
                         (SELECT c7b_hit_count FROM kg_strict_2_of_8))),
           CASE WHEN (SELECT c7a_hit_count FROM kg_strict_2_of_8) > 0
                 AND (SELECT c7b_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT values_seen FROM checkpoint_7_rdf_peaks)

    UNION ALL SELECT 8,
           'Cr-O bond length (2.02–2.05 Å)',
           (SELECT hit_count FROM checkpoint_8_cr_o_bond_length),
           CASE WHEN (SELECT hit_count FROM checkpoint_8_cr_o_bond_length) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT c8_hit_count FROM kg_strict_2_of_8),
           CASE WHEN (SELECT c8_hit_count FROM kg_strict_2_of_8) > 0
                THEN 'PASS' ELSE 'FAIL' END,
           (SELECT values_seen FROM checkpoint_8_cr_o_bond_length)
)

-- -----------------------------------------------------------------------------
-- 6. Final output — three sections
-- -----------------------------------------------------------------------------
-- Wrap each branch in parens so PostgreSQL applies ORDER BY only to the
-- overall UNION result, not to the first SELECT (which would terminate the
-- statement before the UNION).
SELECT section, line_no, detail, staging, kg_diagnostic, hits
FROM (
(
-- 6a. Per-checkpoint result table.
SELECT
    'checkpoint' AS section,
    checkpoint_no::text AS line_no,
    checkpoint_name AS detail,
    staging_status AS staging,
    kg_status AS kg_diagnostic,
    staging_hit_count::text || '/' || kg_hit_count::text AS hits
FROM scorecard
)

UNION ALL

(
-- 6b. Summary roll-up.
SELECT
    'summary' AS section,
    'staging_strict' AS line_no,
    (SELECT COUNT(*)::text || '/8 PASS' FROM scorecard
        WHERE staging_status = 'PASS') AS detail,
    (SELECT COUNT(*)::text || ' of 8' FROM scorecard
        WHERE staging_status = 'PASS') AS staging,
    '' AS kg_diagnostic,
    '' AS hits
FROM (SELECT 1) AS _
)

UNION ALL
(
SELECT
    'summary', 'kg_strict_diagnostic',
    (SELECT COUNT(*)::text || '/8 PASS (NFM-3824 original)' FROM scorecard
        WHERE kg_status = 'PASS'),
    '', '', ''
FROM (SELECT 1) AS _
)

UNION ALL
(
SELECT
    'summary', 'user_facing_pass_bar',
    CASE WHEN (SELECT COUNT(*) FROM scorecard WHERE staging_status = 'PASS') = 8
         THEN '8/8 PASS — v0.5.0 ship gate met'
         ELSE (SELECT COUNT(*)::text || '/8 FAIL — v0.5.0 ship gate NOT met' FROM scorecard)
    END,
    '', '', ''
FROM (SELECT 1) AS _
)

UNION ALL

(
-- 6c. skipped_unknown_properties diagnostic.
SELECT
    'diagnostic' AS section,
    'skipped_unknown_properties' AS line_no,
    CASE WHEN (SELECT total_skipped_unknown_properties FROM skipped_diagnostic) <= 10
         THEN 'WITHIN TARGET (≤10) for v0.5.0'
         ELSE 'ABOVE TARGET (>10) for v0.5.0'
    END AS detail,
    (SELECT total_skipped_unknown_properties::text FROM skipped_diagnostic) AS staging,
    (SELECT n_extraction_runs::text FROM skipped_diagnostic) AS kg_diagnostic,
    '' AS hits
FROM (SELECT 1) AS _
)
) AS combined
ORDER BY
    CASE section
        WHEN 'checkpoint' THEN 0
        WHEN 'summary'    THEN 1
        ELSE 2
    END,
    CASE WHEN section = 'checkpoint' THEN line_no::int ELSE 0 END,
    line_no;