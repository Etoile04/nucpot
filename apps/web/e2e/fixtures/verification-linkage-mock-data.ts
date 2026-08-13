/**
 * Mock API response data for Verification Linkage E2E tests (NFM-1752).
 *
 * Mirrors the Pydantic response schemas from:
 *   - apps/api/src/nfm_db/schemas/verification_task.py
 *   - apps/api/src/nfm_db/models/verification_task.py
 *
 * Usage:
 *   import { MOCK_TASK_CREATED } from './verification-linkage-mock-data'
 */

import { wrapSuccess } from "./design-workspace-mock-data"

// =============================================================================
// IDs
// =============================================================================

export const MOCK_TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
export const MOCK_TASK_ID_FAILED = "f1e2d3c4-b5a6-7890-fecd-ba0987654321"

// =============================================================================
// POST /api/v1/verification/tasks — create response (queued)
// =============================================================================

export const MOCK_TASK_CREATED = {
  id: MOCK_TASK_ID,
  composition: { U: 0.75, Mo: 0.1, Nb: 0.08, Zr: 0.04, Ti: 0.03 },
  potential_function: "EAM",
  temperature_min: 300.0,
  temperature_max: 1200.0,
  timestep_count: 10000,
  status: "queued",
  rating: null,
  rating_summary: null,
  rating_metrics: null,
  error_message: null,
  created_at: "2026-07-22T10:00:00Z",
  updated_at: "2026-07-22T10:00:00Z",
}

// =============================================================================
// GET /api/v1/verification/tasks/{id} — status progression
// =============================================================================

export const MOCK_TASK_RUNNING = {
  ...MOCK_TASK_CREATED,
  status: "running",
  updated_at: "2026-07-22T10:01:00Z",
}

export const MOCK_TASK_COMPLETED_A = {
  ...MOCK_TASK_CREATED,
  status: "completed",
  rating: "A",
  rating_summary:
    "Excellent structural stability. RDF peaks match reference within 2%. No defects detected.",
  rating_metrics: {
    rdf_match_pct: 98.5,
    defect_density: 0.0,
    energy_drift_pct: 0.12,
    lattice_constant_error_pct: 0.3,
  },
  updated_at: "2026-07-22T10:05:00Z",
}

export const MOCK_TASK_COMPLETED_F = {
  id: MOCK_TASK_ID_FAILED,
  composition: { U: 0.75, Mo: 0.1, Nb: 0.08, Zr: 0.04, Ti: 0.03 },
  potential_function: "EAM",
  temperature_min: 300.0,
  temperature_max: 1200.0,
  timestep_count: 10000,
  status: "completed",
  rating: "F",
  rating_summary:
    "Simulation failed: LAMMPS lost atoms at step 3421. Potential unstable for this composition.",
  rating_metrics: {
    rdf_match_pct: 0,
    defect_density: null,
    energy_drift_pct: null,
    lattice_constant_error_pct: null,
  },
  error_message: "LAMMPS error: Lost atoms: original 3125 current 3124",
  updated_at: "2026-07-22T10:03:00Z",
  created_at: "2026-07-22T10:00:00Z",
}

// =============================================================================
// Error responses
// =============================================================================

export const TASK_CREATION_ERROR = {
  success: false,
  error: "Internal server error: failed to enqueue verification task",
}

export const TASK_NOT_FOUND_ERROR = {
  success: false,
  error: "Verification task not-found-id not found",
}
