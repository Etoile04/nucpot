export interface Potential {
  id: string
  name: string
  display_name: string | null
  type: string
  subtype: string | null
  format: string | null
  elements: string[]
  system_name: string | null
  system_tags: string[] | null
  description: string | null
  applicability: Applicability | null
  references: Reference[]
  developers: Developer[]
  verified_props: Record<string, unknown>
  sim_software: string[]
  lammps_config: LammpsConfig | null
  file_url: string | null
  file_hash: string | null
  file_size: number | null
  source: string | null
  license: string
  tags: string[]
  extra: PotentialExtra | null
  status: string
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface Applicability {
  temperatureRange?: [number, number]
  pressureRange?: [number, number]
  phases?: string[]
  notes?: string
}

export interface Reference {
  doi?: string
  citation?: string
  url?: string
}

export interface Developer {
  name: string
  affiliation?: string
}

export interface LammpsConfig {
  pair_style?: string
  pair_coeff?: string
  note?: string
}

export interface PotentialExtra {
  irradiationRelevant?: boolean
  hasDefectData?: boolean
  hasLiquidPhase?: boolean
  validationLevel?: 'basic' | 'benchmarked' | 'production'
}

export interface PotentialsResponse {
  potentials: Potential[]
  total: number
  page: number
  limit: number
  totalPages: number
}

export interface StatsResponse {
  totalPotentials: number
  totalTypes: number
  totalElements: number
  types: string[]
  elements: string[]
  recent: Potential[]
}

// Phase 2: Auth & Contributions

export interface Profile {
  id: string
  username: string
  full_name: string | null
  email: string | null
  role: 'admin' | 'contributor' | 'viewer'
  avatar_url: string | null
  created_at: string
  updated_at: string
}

export interface Contribution {
  id: string
  potential_id: string | null
  user_id: string | null
  action: 'create' | 'update' | 'review'
  status: 'pending' | 'approved' | 'rejected'
  notes: string | null
  created_at: string
}

export interface AuthUser {
  id: string
  email: string | null
  profile: Profile | null
}

// Phase 2: Verification Pipeline

export type VerificationGrade = 'A' | 'B' | 'C' | 'D' | 'F'
export type VerificationStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface PropertyVerification {
  value: number
  unit: string
  reference: number
  error_pct: number
  grade: VerificationGrade
}

export interface Verification {
  id: string
  potential_id: string
  status: VerificationStatus
  results: Record<string, PropertyVerification>
  overall_grade: VerificationGrade | null
  summary: string | null
  error_log: string | null
  compute_time: number | null
  requested_by: string | null
  created_at: string | null
  completed_at: string | null
}

export interface ReferenceValue {
  id: string
  element_system: string
  phase: string | null
  property: string
  value: number
  unit: string | null
  uncertainty: number | null
  temperature: number | null
  source: string | null
  source_doi: string | null
  method: string | null
}
