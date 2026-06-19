/** API client for potential upload endpoints (NFM-299 write path).
 * Follows the potentials-api.ts pattern.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"

export interface PotentialMetadata {
  name: string
  display_name?: string
  type: string
  subtype?: string
  format?: string
  elements: string[]
  system_name: string
  description: string
  system_tags?: string[]
  applicability?: Record<string, unknown>
  references?: Record<string, unknown>[]
  developers?: Record<string, unknown>[]
  lammps_config?: Record<string, unknown>
  tags?: string[]
  extra?: Record<string, unknown>
  license_type: "own_work" | "author_permission" | "open_license"
  license_detail?: string
  auth_file_path?: string
  uploaded_by?: string
}

export interface CreatedPotential {
  id: string
  name: string
  type: string
  elements: string[]
  system_name?: string
  description?: string
  status?: string
  extra?: Record<string, unknown>
}

export interface FileInfo {
  file_name: string
  file_url: string
  file_hash: string
  file_size: number
}

interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}

export type SubmitResult =
  | { success: true; potential: CreatedPotential }
  | { success: false; error: string; status?: number }

export type UploadResult =
  | { success: true; file_info: FileInfo }
  | { success: false; error: string; status?: number }

/** Submit potential metadata.  Returns the created potential or an error. */
export async function submitPotential(metadata: PotentialMetadata): Promise<SubmitResult> {
  const response = await fetch(`${API_BASE}/api/v1/potentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(metadata),
  })
  const json: ApiResponse<CreatedPotential> = await response.json().catch(() => ({
    success: false,
    error: `Request failed: ${response.status}`,
  }))
  if (!response.ok || !json.success || !json.data) {
    return { success: false, error: json.error ?? `Request failed: ${response.status}`, status: response.status }
  }
  return { success: true, potential: json.data }
}

/** Upload the potential file after metadata creation.  Returns file info or an error. */
export async function uploadPotentialFile(potentialId: string, file: File): Promise<UploadResult> {
  const formData = new FormData()
  formData.append("file", file)
  const response = await fetch(`${API_BASE}/api/v1/potentials/${potentialId}/file`, {
    method: "POST",
    body: formData,
  })
  const json: ApiResponse<FileInfo> = await response.json().catch(() => ({
    success: false,
    error: `Request failed: ${response.status}`,
  }))
  if (!response.ok || !json.success || !json.data) {
    return { success: false, error: json.error ?? `Request failed: ${response.status}`, status: response.status }
  }
  return { success: true, file_info: json.data }
}
