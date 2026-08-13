/** MD verification API client for Phase 3 frontend integration. */

// Relative paths — next.config.ts rewrite proxy handles backend routing.

// =============================================================================
// Type Definitions
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

export enum DefectType {
  VACANCY = "vacancy",
  INTERSTITIAL = "interstitial",
  DISLOCATION = "dislocation",
  GRAIN_BOUNDARY = "grain_boundary",
  OTHER = "other",
}

export enum FittingMethod {
  ARC_DPA = "arc-dpa",
  RPA = "RPA",
  OTHER = "other",
}

// =============================================================================
// API Request/Response Types
// =============================================================================

export interface MDVerificationJobSubmitRequest {
  potential_id: string
  element_system: string
  phase?: string
  potential_file: string
  structure_file: string
  config: SimulationConfig
  priority?: number
}

export interface SimulationConfig {
  temperature: number
  pressure: number
  simulation_time?: number
  timestep?: number
  ensemble?: string
}

export interface MDVerificationJobResponse {
  id: string
  potential_id: string
  element_system: string
  phase: string | null
  config: SimulationConfig
  status: JobStatus
  priority: number
  submitted_at: string | null
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface MDVerificationJobListResponse {
  jobs: MDVerificationJobResponse[]
  total: number
  limit: number
  offset: number
}

export interface JobStatusResponse {
  job_id: string
  status: JobStatus
  submitted_at: string | null
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  hpc_job_status: HpcJobStatus | null
  hpc_cluster: string | null
}

export interface MDSimulationResultResponse {
  id: string
  verification_job_id: string
  trajectory_file_path: string | null
  thermodynamic_data: Record<string, unknown> | null
  simulation_time_ps: number | null
  steps_completed: number | null
  final_energy: number | null
  final_temperature: number | null
  final_pressure: number | null
  created_at: string
}

export interface DefectAnalysisResultResponse {
  id: string
  verification_job_id: string
  defect_type: DefectType
  concentration: number
  formation_energy: number | null
  metadata: Record<string, unknown> | null
}

export interface PotentialFittingResultResponse {
  id: string
  verification_job_id: string
  fitting_method: FittingMethod
  parameters: Record<string, unknown>
  quality_metrics: Record<string, unknown> | null
  created_at: string
}

// =============================================================================
// API Client
// =============================================================================

interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}

/**
 * Get authentication token from localStorage
 */
function getAuthToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem("auth_token")
}

/**
 * Get common headers for API requests
 */
function getHeaders(): HeadersInit {
  const headers: HeadersInit = {
    "Content-Type": "application/json",
  }

  const token = getAuthToken()
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }

  return headers
}

/**
 * Submit a new MD verification job
 */
export async function submitMDVerificationJob(
  payload: MDVerificationJobSubmitRequest,
): Promise<MDVerificationJobResponse> {
  const response = await fetch(`/api/v1/md-verification/jobs`, {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null)
    const message =
      errorBody?.detail ??
      errorBody?.error ??
      `提交失败 (${response.status})`
    throw new Error(message)
  }

  const result: ApiResponse<MDVerificationJobResponse> = await response.json()

  if (!result.success || !result.data) {
    throw new Error(result.error ?? "提交失败")
  }

  return result.data
}

/**
 * List MD verification jobs with optional filters
 */
export async function listMDVerificationJobs(
  params: {
    potential_id?: string
    status?: JobStatus
    element_system?: string
    limit?: number
    offset?: number
  } = {},
): Promise<MDVerificationJobListResponse> {
  const queryParams = new URLSearchParams()

  if (params.potential_id) queryParams.append("potential_id", params.potential_id)
  if (params.status) queryParams.append("status", params.status)
  if (params.element_system) queryParams.append("element_system", params.element_system)
  if (params.limit) queryParams.append("limit", params.limit.toString())
  if (params.offset) queryParams.append("offset", params.offset.toString())

  const response = await fetch(
    `/api/v1/md-verification/jobs?${queryParams.toString()}`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )

  if (!response.ok) {
    throw new Error(`获取任务列表失败 (${response.status})`)
  }

  return response.json()
}

/**
 * Get a single MD verification job by ID
 */
export async function getMDVerificationJob(
  jobId: string,
): Promise<MDVerificationJobResponse> {
  const response = await fetch(
    `/api/v1/md-verification/jobs/${jobId}`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )

  if (!response.ok) {
    throw new Error(`获取任务详情失败 (${response.status})`)
  }

  return response.json()
}

/**
 * Get the status of an MD verification job
 */
export async function getMDVerificationJobStatus(
  jobId: string,
): Promise<JobStatusResponse> {
  const response = await fetch(
    `/api/v1/md-verification/jobs/${jobId}/status`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )

  if (!response.ok) {
    throw new Error(`获取任务状态失败 (${response.status})`)
  }

  return response.json()
}

/**
 * Cancel an MD verification job
 */
export async function cancelMDVerificationJob(
  jobId: string,
): Promise<{ job_id: string; previous_status: JobStatus; new_status: JobStatus }> {
  const response = await fetch(
    `/api/v1/md-verification/jobs/${jobId}`,
    {
      method: "DELETE",
      headers: getHeaders(),
    },
  )

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null)
    const message = errorBody?.detail ?? `取消任务失败 (${response.status})`
    throw new Error(message)
  }

  return response.json()
}

/**
 * Get simulation results for a job
 */
export async function getSimulationResults(
  jobId: string,
): Promise<MDSimulationResultResponse> {
  const response = await fetch(
    `/api/v1/md-verification/jobs/${jobId}/simulation`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )

  if (!response.ok) {
    throw new Error(`获取模拟结果失败 (${response.status})`)
  }

  return response.json()
}

/**
 * Get defect analysis results for a job
 */
export async function getDefectAnalysisResults(
  jobId: string,
  defectType?: DefectType,
): Promise<DefectAnalysisResultResponse[]> {
  const queryParams = defectType ? `?defect_type=${defectType}` : ""

  const response = await fetch(
    `/api/v1/md-verification/jobs/${jobId}/defects${queryParams}`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )

  if (!response.ok) {
    throw new Error(`获取缺陷分析结果失败 (${response.status})`)
  }

  return response.json()
}

/**
 * Get potential fitting results for a job
 */
export async function getFittingResults(
  jobId: string,
  fittingMethod?: FittingMethod,
): Promise<PotentialFittingResultResponse[]> {
  const queryParams = fittingMethod ? `?fitting_method=${fittingMethod}` : ""

  const response = await fetch(
    `/api/v1/md-verification/jobs/${jobId}/fitting${queryParams}`,
    {
      method: "GET",
      headers: getHeaders(),
    },
  )

  if (!response.ok) {
    throw new Error(`获取势函数拟合结果失败 (${response.status})`)
  }

  return response.json()
}
