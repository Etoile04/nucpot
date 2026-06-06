-- Migration 006: NFMD Review tables + RPCs
-- Source: ThinkStation NFMD database (nfmd-postgres:15432)
-- Required by: /admin/review page, review-api.ts, /api/admin/review route

-- ── Tables ────────────────────────────────────────────────────────────────────

-- Parameters table: material property data extracted from literature
CREATE TABLE IF NOT EXISTS parameters (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  name_zh         TEXT,
  name_en         TEXT,
  symbol          TEXT,
  category        TEXT NOT NULL,
  subcategory     TEXT,
  value_type      TEXT NOT NULL,
  value_scalar    NUMERIC(20,10),
  value_min       NUMERIC(20,10),
  value_max       NUMERIC(20,10),
  value_expr      TEXT,
  value_list      JSONB,
  value_text      TEXT,
  value_str       TEXT,
  unit            TEXT,
  uncertainty     TEXT,
  material_id     UUID,
  material_raw    TEXT,
  temperature_k   NUMERIC(10,2),
  temperature_str TEXT,
  burnup_range    TEXT,
  method          TEXT,
  confidence      TEXT,
  source_file     TEXT,
  equation        TEXT,
  notes           TEXT,
  ts_vector       TSVECTOR,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  review_status   TEXT,
  review_reason   TEXT,
  reviewed_at     TIMESTAMPTZ,
  reviewed_by     TEXT,
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_params_category ON parameters (category);

-- Literature table: reference bibliography entries
CREATE TABLE IF NOT EXISTS literature (
  id              TEXT PRIMARY KEY,
  title           TEXT,
  authors         TEXT,
  journal         TEXT,
  year            INTEGER,
  doi             TEXT,
  file_path       TEXT,
  parameter_count INTEGER DEFAULT 0,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  review_status   TEXT,
  review_notes    TEXT,
  reviewed_at     TIMESTAMPTZ,
  reviewed_by     TEXT
);

CREATE INDEX IF NOT EXISTS idx_literature_doi ON literature (doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_literature_year ON literature (year);

-- RLS for literature
ALTER TABLE literature ENABLE ROW LEVEL SECURITY;
CREATE POLICY "literature_public_read" ON literature FOR SELECT TO anon, authenticated USING (true);

-- Review audit log
CREATE TABLE IF NOT EXISTS review_audit_log (
  id         BIGSERIAL PRIMARY KEY,
  table_name TEXT NOT NULL,
  record_id  TEXT NOT NULL,
  action     TEXT NOT NULL,
  old_status TEXT,
  new_status TEXT,
  changes    JSONB,
  reviewer   TEXT,
  notes      TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_audit_record ON review_audit_log (table_name, record_id);
CREATE INDEX IF NOT EXISTS idx_review_audit_time ON review_audit_log (created_at DESC);

-- ── RPC Functions ─────────────────────────────────────────────────────────────

-- review_stats: aggregate review statistics
CREATE OR REPLACE FUNCTION review_stats()
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_result jsonb;
BEGIN
  SELECT jsonb_build_object(
    'by_status', coalesce(
      (SELECT jsonb_object_agg(status, cnt) FROM (
        SELECT review_status AS status, count(*) AS cnt
        FROM parameters WHERE review_status IS NOT NULL GROUP BY review_status
      ) sub), '{}'::jsonb
    ),
    'by_value_type', coalesce(
      (SELECT jsonb_object_agg(vt, cnt) FROM (
        SELECT value_type AS vt, count(*) AS cnt
        FROM parameters WHERE review_status IS NOT NULL GROUP BY value_type
      ) sub), '{}'::jsonb
    ),
    'top_materials', coalesce(
      (SELECT jsonb_object_agg(mat, cnt) FROM (
        SELECT material_raw AS mat, count(*) AS cnt
        FROM parameters WHERE review_status IS NOT NULL
        GROUP BY material_raw ORDER BY cnt DESC LIMIT 10
      ) sub), '{}'::jsonb
    ),
    'total_in_review', (SELECT count(*) FROM parameters WHERE review_status IS NOT NULL),
    'total_params', (SELECT count(*) FROM parameters)
  ) INTO v_result;

  RETURN v_result;
END;
$$;

-- review_queue_params: paginated parameter list with filters
CREATE OR REPLACE FUNCTION review_queue_params(
  p_status text DEFAULT NULL,
  p_value_type text DEFAULT NULL,
  p_material text DEFAULT NULL,
  p_source_file text DEFAULT NULL,
  p_limit integer DEFAULT 50,
  p_offset integer DEFAULT 0
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_result jsonb;
BEGIN
  SELECT jsonb_build_object(
    'data', coalesce(jsonb_agg(row_to_json(sub)), '[]'::jsonb),
    'total', count(*) OVER()
  ) INTO v_result
  FROM (
    SELECT
      p.id, p.name, p.name_zh, p.category, p.subcategory,
      p.value_type, p.value_scalar, p.value_min, p.value_max,
      p.value_expr, p.value_list, p.value_text,
      p.unit, p.material_raw, p.temperature_k,
      p.confidence, p.source_file, p.notes,
      p.review_status, p.review_reason, p.reviewed_at
    FROM parameters p
    WHERE
      p.review_status IS NOT NULL
      AND (p_status IS NULL OR p.review_status = p_status)
      AND (p_value_type IS NULL OR p.value_type = p_value_type)
      AND (p_material IS NULL OR p.material_raw = p_material)
      AND (p_source_file IS NULL OR p.source_file ILIKE '%' || p_source_file || '%')
    ORDER BY
      CASE p.review_status
        WHEN 'needs_data' THEN 1 WHEN 'pending' THEN 2 WHEN 'needs_review' THEN 3
        WHEN 'duplicate' THEN 4 WHEN 'rejected' THEN 5 WHEN 'approved' THEN 6
        WHEN 'auto_approved' THEN 7 ELSE 8
      END,
      p.reviewed_at ASC NULLS LAST
    LIMIT p_limit OFFSET p_offset
  ) sub;

  RETURN v_result;
END;
$$;

-- review_queue_literature: paginated literature list with filters
CREATE OR REPLACE FUNCTION review_queue_literature(
  p_status text DEFAULT NULL,
  p_limit integer DEFAULT 50,
  p_offset integer DEFAULT 0
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_result jsonb;
BEGIN
  SELECT jsonb_build_object(
    'data', coalesce(jsonb_agg(row_to_json(sub)), '[]'::jsonb),
    'total', count(*) OVER()
  ) INTO v_result
  FROM (
    SELECT l.id, l.title, l.parameter_count,
      l.review_status, l.review_notes, l.reviewed_at
    FROM literature l
    WHERE (p_status IS NULL OR l.review_status = p_status OR l.review_status IS NULL)
    ORDER BY
      CASE l.review_status
        WHEN 'pending' THEN 1 WHEN 'needs_review' THEN 2
        WHEN 'approved' THEN 3 ELSE 4
      END NULLS LAST,
      l.id
    LIMIT p_limit OFFSET p_offset
  ) sub;

  RETURN v_result;
END;
$$;

-- review_literature_for_source: find literature matching a source file name
CREATE OR REPLACE FUNCTION review_literature_for_source(p_source_file text)
RETURNS TABLE(id text, title text, authors text, journal text, year integer, doi text, match_method text)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_key TEXT;
    v_year TEXT;
    v_doi_pattern TEXT;
BEGIN
    SELECT regexp_replace(regexp_replace(p_source_file, '^.+/', ''), '\.(md|txt|pdf)$', '')
    INTO v_key;

    RETURN QUERY
    SELECT l.id, l.title, l.authors, l.journal, l.year, l.doi, 'exact_basename'::TEXT
    FROM literature l
    WHERE l.id = v_key;
    IF FOUND THEN RETURN; END IF;

    RETURN QUERY
    SELECT l.id, l.title, l.authors, l.journal, l.year, l.doi, 'substring_match'::TEXT
    FROM literature l
    WHERE l.id ILIKE '%' || v_key || '%'
       OR v_key ILIKE '%' || l.id || '%'
    LIMIT 3;
    IF FOUND THEN RETURN; END IF;

    v_year := substring(v_key from '(\d{4})');
    RETURN QUERY
    SELECT l.id, l.title, l.authors, l.journal, l.year, l.doi, 'year_author_fuzzy'::TEXT
    FROM literature l
    WHERE (v_year IS NOT NULL AND l.year::TEXT = v_year)
      AND (
        l.authors ILIKE '%' || split_part(v_key, '_', 1) || '%'
        OR l.id ILIKE '%' || split_part(v_key, '_', 1) || '%'
      )
    LIMIT 3;
    IF FOUND THEN RETURN; END IF;

    BEGIN
        IF v_key ~ '^\d+_\d+' THEN
            v_doi_pattern := replace(v_key, '_', '.');
            RETURN QUERY
            SELECT l.id, l.title, l.authors, l.journal, l.year, l.doi, 'doi_restore'::TEXT
            FROM literature l
            WHERE l.doi ILIKE v_doi_pattern || '%';
        END IF;
    END;

    RETURN;
END;
$$;

-- review_batch_update: batch status update with audit
CREATE OR REPLACE FUNCTION review_batch_update(
  p_ids text[],
  p_status text,
  p_reason text DEFAULT NULL,
  p_reviewer text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_count integer;
  v_action text;
BEGIN
  v_action := CASE p_status
    WHEN 'approved' THEN 'approve' WHEN 'rejected' THEN 'reject'
    WHEN 'needs_data' THEN 'needs_data' WHEN 'duplicate' THEN 'duplicate'
    ELSE 'update'
  END;

  INSERT INTO review_audit_log (table_name, record_id, action, old_status, new_status, reviewer, notes)
  SELECT 'parameters', unnest(p_ids), v_action, p.review_status, p_status, p_reviewer, p_reason
  FROM (SELECT DISTINCT unnest(p_ids) AS id) ids
  LEFT JOIN parameters p ON p.id = ids.id
  WHERE p.id IS NOT NULL;

  UPDATE parameters
  SET review_status = p_status, review_reason = COALESCE(p_reason, review_reason),
      reviewed_at = now(), reviewed_by = p_reviewer, updated_at = now()
  WHERE id = ANY(p_ids);

  GET DIAGNOSTICS v_count = ROW_COUNT;

  RETURN jsonb_build_object('updated_count', v_count, 'action', v_action);
END;
$$;

-- review_update_param: update single parameter with audit
CREATE OR REPLACE FUNCTION review_update_param(
  p_id text,
  p_name text DEFAULT NULL,
  p_value_scalar numeric DEFAULT NULL,
  p_value_min numeric DEFAULT NULL,
  p_value_max numeric DEFAULT NULL,
  p_value_expr text DEFAULT NULL,
  p_value_list jsonb DEFAULT NULL,
  p_value_text text DEFAULT NULL,
  p_unit text DEFAULT NULL,
  p_confidence text DEFAULT NULL,
  p_notes text DEFAULT NULL,
  p_review_status text DEFAULT NULL,
  p_reviewer text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_old_status text;
  v_found boolean := false;
BEGIN
  SELECT review_status INTO v_old_status FROM parameters WHERE id = p_id;

  UPDATE parameters SET
    name = COALESCE(p_name, name),
    value_scalar = COALESCE(p_value_scalar, value_scalar),
    value_min = COALESCE(p_value_min, value_min),
    value_max = COALESCE(p_value_max, value_max),
    value_expr = COALESCE(p_value_expr, value_expr),
    value_list = COALESCE(p_value_list, value_list),
    value_text = COALESCE(p_value_text, value_text),
    unit = COALESCE(p_unit, unit),
    confidence = COALESCE(p_confidence, confidence),
    notes = COALESCE(p_notes, notes),
    review_status = COALESCE(p_review_status, review_status),
    review_reason = CASE WHEN p_review_status IS NOT NULL THEN 'manual_update' ELSE review_reason END,
    reviewed_at = CASE WHEN p_review_status IS NOT NULL THEN now() ELSE reviewed_at END,
    reviewed_by = CASE WHEN p_review_status IS NOT NULL THEN p_reviewer ELSE reviewed_by END,
    updated_at = now()
  WHERE id = p_id;

  GET DIAGNOSTICS v_found = ROW_COUNT;

  IF v_found THEN
    INSERT INTO review_audit_log (table_name, record_id, action, old_status, new_status, reviewer, notes)
    VALUES ('parameters', p_id, 'update', v_old_status, COALESCE(p_review_status, v_old_status), p_reviewer, p_notes);
    RETURN jsonb_build_object('success', true, 'message', 'OK');
  ELSE
    RETURN jsonb_build_object('success', false, 'message', 'Not found');
  END IF;
END;
$$;

-- review_auto_classify: auto-classify parameters by confidence and value presence
CREATE OR REPLACE FUNCTION review_auto_classify()
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_total integer := 0;
  v_stats jsonb;
BEGIN
  UPDATE parameters SET
    review_status = 'needs_data', review_reason = 'empty_shell',
    reviewed_by = 'auto_classifier', updated_at = now()
  WHERE review_status IS NULL AND (
    (value_type = 'scalar' AND value_scalar IS NULL) OR
    (value_type = 'range' AND value_min IS NULL AND value_max IS NULL AND value_scalar IS NULL) OR
    (value_type = 'expression' AND (value_expr IS NULL OR value_expr = '')) OR
    (value_type = 'list' AND value_list IS NULL) OR
    (value_type = 'text' AND (value_text IS NULL OR value_text = ''))
  );
  GET DIAGNOSTICS v_total = ROW_COUNT;

  UPDATE parameters SET
    review_status = 'needs_review', review_reason = 'low_confidence',
    reviewed_by = 'auto_classifier', updated_at = now()
  WHERE review_status IS NULL AND confidence = 'low';

  UPDATE parameters SET
    review_status = 'auto_approved', review_reason = 'auto: high + value',
    reviewed_by = 'auto_classifier', updated_at = now()
  WHERE review_status IS NULL AND confidence = 'high' AND (
    (value_type = 'scalar' AND value_scalar IS NOT NULL) OR
    (value_type = 'range' AND (value_min IS NOT NULL OR value_max IS NOT NULL)) OR
    (value_type = 'expression' AND value_expr IS NOT NULL) OR
    (value_type = 'list' AND value_list IS NOT NULL) OR
    (value_type = 'text' AND value_text IS NOT NULL)
  );

  UPDATE parameters SET
    review_status = 'auto_approved', review_reason = 'auto: medium + value',
    reviewed_by = 'auto_classifier', updated_at = now()
  WHERE review_status IS NULL AND confidence = 'medium' AND (
    (value_type = 'scalar' AND value_scalar IS NOT NULL) OR
    (value_type = 'range' AND (value_min IS NOT NULL OR value_max IS NOT NULL)) OR
    (value_type = 'expression' AND value_expr IS NOT NULL) OR
    (value_type = 'list' AND value_list IS NOT NULL) OR
    (value_type = 'text' AND value_text IS NOT NULL)
  );

  UPDATE parameters SET
    review_status = 'pending', review_reason = 'auto: unclassified',
    reviewed_by = 'auto_classifier', updated_at = now()
  WHERE review_status IS NULL;

  SELECT jsonb_object_agg(status, cnt) INTO v_stats FROM (
    SELECT review_status AS status, count(*) AS cnt
    FROM parameters WHERE review_status IS NOT NULL GROUP BY review_status
  ) sub;

  RETURN jsonb_build_object('classified_total', v_total, 'status_counts', v_stats);
END;
$$;
