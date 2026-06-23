/**
 * Mock data fixtures for MD Verification E2E tests.
 *
 * Covers four scenarios:
 * - Normal: successful submission, completion with results
 * - QueueFull: HPC queue at capacity
 * - Timeout: job exceeds maximum runtime
 * - Error: server-side / API failure
 */

// =============================================================================
// Re-exported enums (avoids @/ path alias in e2e context)
// =============================================================================

export enum JobStatus {
  PENDING = "pending",
  SUBMITTED = "submitted",
  RUNNING = "running",
  COMPLETED = "completed",
  FAILED = "failed",
}

export enum HpcJobStatus {
  PENDING = "pending",
  RUNNING = "running",
  COMPLETED = "completed",
  FAILED = "failed",
  CANCELLED = "cancelled",
}

// =============================================================================
// Shared timestamps
// =============================================================================

const NOW = new Date().toISOString()
const ONE_HOUR_AGO = new Date(Date.now() - 3600_000).toISOString()
const TWO_HOURS_AGO = new Date(Date.now() - 7200_000).toISOString()

// =============================================================================
// Normal scenario fixtures
// =============================================================================

export const MOCK_SUBMITTED_JOB = {
  id: "mock-job-submitted-001",
  potential_id: "EAM_alloy_U_test",
  element_system: "U",
  phase: "BCC",
  potential_file: "/data/potentials/U_U.empirical",
  structure_file: "/data/structures/BCC_U.cif",
  config: {
    temperature: 300,
    pressure: 0,
    simulation_time: 100,
    timestep: 0.001,
    ensemble: "NPT",
  },
  priority: 5,
  status: JobStatus.SUBMITTED,
  submitted_at: NOW,
  started_at: null,
  completed_at: null,
  error_message: null,
  created_at: NOW,
  updated_at: NOW,
}

export const MOCK_RUNNING_JOB = {
  ...MOCK_SUBMITTED_JOB,
  id: "mock-job-running-002",
  status: JobStatus.RUNNING,
  started_at: ONE_HOUR_AGO,
}

export const MOCK_COMPLETED_JOB = {
  ...MOCK_SUBMITTED_JOB,
  id: "mock-job-completed-003",
  status: JobStatus.COMPLETED,
  started_at: TWO_HOURS_AGO,
  submitted_at: TWO_HOURS_AGO,
  completed_at: NOW,
}

export const MOCK_RUNNING_STATUS = {
  job_id: "mock-job-running-002",
  status: JobStatus.RUNNING,
  submitted_at: ONE_HOUR_AGO,
  started_at: ONE_HOUR_AGO,
  completed_at: null,
  error_message: null,
  hpc_job_status: HpcJobStatus.RUNNING,
  hpc_cluster: "星逸集群",
}

export const MOCK_COMPLETED_JOB_STATUS = {
  job_id: "mock-job-completed-003",
  status: JobStatus.COMPLETED,
  submitted_at: TWO_HOURS_AGO,
  started_at: TWO_HOURS_AGO,
  completed_at: NOW,
  error_message: null,
  hpc_job_status: HpcJobStatus.COMPLETED,
  hpc_cluster: "星逸集群",
}

export const MOCK_SIMULATION_RESULTS = {
  id: "sim-result-001",
  verification_job_id: "mock-job-completed-003",
  trajectory_file_path: "/data/results/mock-job-completed-003/trajectory.lammpstrj",
  thermodynamic_data: {
    energy: Array.from({ length: 50 }, (_, i) => ({
      step: (i + 1) * 200,
      energy: -8.35 - 0.02 * Math.sin(i * 0.3) + i * 0.0001,
    })),
    temperature: Array.from({ length: 50 }, (_, i) => ({
      step: (i + 1) * 200,
      temperature: 298 + 2 * Math.sin(i * 0.2),
    })),
    pressure: Array.from({ length: 50 }, (_, i) => ({
      step: (i + 1) * 200,
      pressure: 0.01 + 0.005 * Math.cos(i * 0.15),
    })),
  },
  simulation_time_ps: 100,
  steps_completed: 10000,
  final_energy: -8.3482,
  final_temperature: 299.7,
  final_pressure: 0.012,
  created_at: NOW,
}

export const MOCK_DEFECT_RESULTS = [
  {
    id: "defect-001",
    verification_job_id: "mock-job-completed-003",
    defect_type: "vacancy",
    concentration: 0.000234,
    formation_energy: 3.52,
    metadata: { method: "NEB", relaxed: true },
  },
  {
    id: "defect-002",
    verification_job_id: "mock-job-completed-003",
    defect_type: "interstitial",
    concentration: 0.000089,
    formation_energy: 4.18,
    metadata: { method: "NEB", relaxed: true },
  },
  {
    id: "defect-003",
    verification_job_id: "mock-job-completed-003",
    defect_type: "dislocation",
    concentration: 0.000045,
    formation_energy: null,
    metadata: { burgers_vector: "1/2<111>" },
  },
]

export const MOCK_FITTING_RESULTS = [
  {
    id: "fit-001",
    verification_job_id: "mock-job-completed-003",
    fitting_method: "arc-dpa",
    parameters: {
      cutoff: 5.5,
      epsilon: 0.45,
      sigma: 2.2,
      a: 1.2,
      b: 0.8,
      c: 2.1,
      d: 1.5,
    },
    quality_metrics: {
      rmse: 0.023,
      r_squared: 0.987,
      max_error: 0.056,
    },
    created_at: NOW,
  },
]

export const MOCK_JOB_LIST_RESPONSE = {
  jobs: [MOCK_COMPLETED_JOB, MOCK_RUNNING_JOB, MOCK_SUBMITTED_JOB],
  total: 3,
  limit: 10,
  offset: 0,
}

// =============================================================================
// Queue-full scenario
// =============================================================================

export const QUEUE_FULL_ERROR_RESPONSE = {
  success: false,
  error: "HPC 集群队列已满，请稍后重试或降低优先级",
  detail: "queue_capacity_exceeded",
}

// =============================================================================
// Timeout scenario
// =============================================================================

export const MOCK_TIMEOUT_JOB = {
  ...MOCK_SUBMITTED_JOB,
  id: "mock-job-timeout-004",
  status: JobStatus.FAILED,
  error_message: "模拟运行超时：超过最大运行时间限制 (72 小时)",
  started_at: TWO_HOURS_AGO,
  submitted_at: TWO_HOURS_AGO,
  completed_at: NOW,
}

export const MOCK_TIMEOUT_STATUS = {
  job_id: "mock-job-timeout-004",
  status: JobStatus.FAILED,
  submitted_at: TWO_HOURS_AGO,
  started_at: TWO_HOURS_AGO,
  completed_at: NOW,
  error_message: "模拟运行超时：超过最大运行时间限制 (72 小时)",
  hpc_job_status: HpcJobStatus.FAILED,
  hpc_cluster: "星逸集群",
}

// =============================================================================
// Error / server failure scenario
// =============================================================================

export const SERVER_ERROR_RESPONSE = {
  success: false,
  error: "服务内部错误，请稍后重试",
}

export const VALIDATION_ERROR_RESPONSE = {
  success: false,
  error: "势函数文件路径不存在: /invalid/path/test.empirical",
  detail: "file_not_found",
}

// =============================================================================
// Helper: build an ApiResponse envelope
// =============================================================================

export function wrapSuccess<T>(data: T) {
  return { success: true, data }
}
