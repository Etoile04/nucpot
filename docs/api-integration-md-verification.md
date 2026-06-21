# MD Verification API Integration Guide

## Overview

This guide explains how to integrate with the MD Verification API endpoints for programmatic access to verification tasks, status monitoring, and results retrieval.

## Base URL

```typescript
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000"
```

All endpoints are prefixed with `/api/v1/md-verification`

## Authentication

All API requests require JWT authentication:

```typescript
const token = localStorage.getItem("auth_token")

const headers = {
  "Content-Type": "application/json",
  "Authorization": `Bearer ${token}`
}
```

## API Endpoints

### 1. Submit Verification Task

Submit a new MD verification job to the queue.

**Endpoint**: `POST /api/v1/md-verification/jobs`

**Request Body**:

```typescript
interface MDVerificationJobSubmitRequest {
  potential_id: string              // Potential function identifier
  element_system: string           // Element system (e.g., "U", "Pu", "U-Pu")
  phase?: string                    // Optional phase structure
  potential_file: string           // Absolute path to potential file
  structure_file: string           // Absolute path to structure file
  config: {
    temperature: number             // Temperature in Kelvin
    pressure: number                // Pressure in GPa
    simulation_time?: number        // Optional: simulation time in ps
    timestep?: number               // Optional: timestep in ps
    ensemble?: string               // Optional: NPT, NVT, or NVE
  }
  priority?: number                 // Optional: 1-10, default 5
}
```

**Response**:

```typescript
interface MDVerificationJobResponse {
  id: string                        // Generated job ID
  potential_id: string
  element_system: string
  phase: string | null
  config: SimulationConfig
  status: JobStatus                 // pending, submitted, running, completed, failed
  priority: number
  submitted_at: string | null
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}
```

**Example**:

```typescript
import { submitMDVerificationJob } from '@/lib/md-verification-api'

const job = await submitMDVerificationJob({
  potential_id: 'EAM_alloy_U',
  element_system: 'U',
  phase: 'BCC',
  potential_file: '/data/potentials/U_U.empirical',
  structure_file: '/data/structures/BCC_U.cif',
  config: {
    temperature: 300,
    pressure: 0,
    simulation_time: 100,
    timestep: 0.001,
    ensemble: 'NPT'
  },
  priority: 5
})

console.log(`Job submitted: ${job.id}`)
```

### 2. List Verification Tasks

Retrieve a paginated list of verification jobs with optional filtering.

**Endpoint**: `GET /api/v1/md-verification/jobs`

**Query Parameters**:

```typescript
interface ListJobsParams {
  potential_id?: string    // Filter by potential ID
  status?: JobStatus       // Filter by status
  element_system?: string  // Filter by element system
  limit?: number           // Page size (default: 20)
  offset?: number          // Pagination offset (default: 0)
}
```

**Response**:

```typescript
interface MDVerificationJobListResponse {
  jobs: MDVerificationJobResponse[]
  total: number            // Total matching jobs
  limit: number            // Page size
  offset: number           // Current offset
}
```

**Example**:

```typescript
import { listMDVerificationJobs } from '@/lib/md-verification-api'

// List all running jobs
const running = await listMDVerificationJobs({
  status: 'running',
  limit: 10
})

// List completed jobs for a specific potential
const completed = await listMDVerificationJobs({
  potential_id: 'EAM_alloy_U',
  status: 'completed'
})
```

### 3. Get Job Details

Retrieve detailed information about a specific verification job.

**Endpoint**: `GET /api/v1/md-verification/jobs/{job_id}`

**Response**: `MDVerificationJobResponse`

**Example**:

```typescript
import { getMDVerificationJob } from '@/lib/md-verification-api'

const job = await getMDVerificationJob('job-abc-123')
console.log(`Job status: ${job.status}`)
console.log(`Config:`, job.config)
```

### 4. Get Job Status

Retrieve the current status of a verification job, including HPC job information.

**Endpoint**: `GET /api/v1/md-verification/jobs/{job_id}/status`

**Response**:

```typescript
interface JobStatusResponse {
  job_id: string
  status: JobStatus
  submitted_at: string | null
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  hpc_job_status: HpcJobStatus | null    // pending, running, completed, failed, cancelled
  hpc_cluster: string | null              // HPC cluster name
}
```

**Example**:

```typescript
import { getMDVerificationJobStatus } from '@/lib/md-verification-api'

const status = await getMDVerificationJobStatus('job-abc-123')

if (status.hpc_job_status === 'running') {
  console.log(`Running on cluster: ${status.hpc_cluster}`)
}
```

### 5. Cancel Job

Cancel a pending or running verification job.

**Endpoint**: `DELETE /api/v1/md-verification/jobs/{job_id}`

**Response**:

```typescript
interface CancelJobResponse {
  job_id: string
  previous_status: JobStatus
  new_status: JobStatus    // Should be 'cancelled' or 'failed'
}
```

**Example**:

```typescript
import { cancelMDVerificationJob } from '@/lib/md-verification-api'

const result = await cancelMDVerificationJob('job-abc-123')
console.log(`Job ${result.job_id} cancelled`)
```

### 6. Get Simulation Results

Retrieve LAMMPS simulation results for a completed job.

**Endpoint**: `GET /api/v1/md-verification/jobs/{job_id}/simulation`

**Response**:

```typescript
interface MDSimulationResultResponse {
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
```

**Example**:

```typescript
import { getSimulationResults } from '@/lib/md-verification-api'

const results = await getSimulationResults('job-abc-123')

console.log(`Simulation completed: ${results.steps_completed} steps`)
console.log(`Final energy: ${results.final_energy} eV`)
console.log(`Final temperature: ${results.final_temperature} K`)
```

### 7. Get Defect Analysis Results

Retrieve defect analysis results for a completed job.

**Endpoint**: `GET /api/v1/md-verification/jobs/{job_id}/defects`

**Query Parameters**:
- `defect_type` (optional): Filter by defect type (vacancy, interstitial, dislocation, grain_boundary, other)

**Response**:

```typescript
interface DefectAnalysisResultResponse {
  id: string
  verification_job_id: string
  defect_type: DefectType
  concentration: number
  formation_energy: number | null
  metadata: Record<string, unknown> | null
}
```

**Example**:

```typescript
import { getDefectAnalysisResults } from '@/lib/md-verification-api'

// Get all defects
const allDefects = await getDefectAnalysisResults('job-abc-123')

// Get only vacancies
const vacancies = await getDefectAnalysisResults('job-abc-123', 'vacancy')

vacancies.forEach(defect => {
  console.log(`Formation energy: ${defect.formation_energy} eV`)
})
```

### 8. Get Potential Fitting Results

Retrieve potential fitting results for a completed job.

**Endpoint**: `GET /api/v1/md-verification/jobs/{job_id}/fitting`

**Query Parameters**:
- `fitting_method` (optional): Filter by method (arc-dpa, RPA, other)

**Response**:

```typescript
interface PotentialFittingResultResponse {
  id: string
  verification_job_id: string
  fitting_method: FittingMethod
  parameters: Record<string, unknown>
  quality_metrics: Record<string, unknown> | null
  created_at: string
}
```

**Example**:

```typescript
import { getFittingResults } from '@/lib/md-verification-api'

const results = await getFittingResults('job-abc-123')

results.forEach(result => {
  console.log(`Method: ${result.fitting_method}`)
  console.log(`Parameters:`, result.parameters)
  console.log(`Quality:`, result.quality_metrics)
})
```

## Error Handling

All API functions throw `Error` on failure. Implement proper error handling:

```typescript
try {
  const job = await submitMDVerificationJob(payload)
  console.log('Success:', job.id)
} catch (error) {
  if (error instanceof Error) {
    console.error('Submission failed:', error.message)
    
    // Handle specific error cases
    if (error.message.includes('401')) {
      console.error('Authentication required')
    } else if (error.message.includes('404')) {
      console.error('Resource not found')
    } else if (error.message.includes('500')) {
      console.error('Server error - please try again later')
    }
  }
}
```

## Rate Limiting

API endpoints may be rate-limited. Implement exponential backoff for retries:

```typescript
async function submitWithRetry(payload: MDVerificationJobSubmitRequest, maxRetries = 3) {
  let delay = 1000 // Start with 1 second delay
  
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await submitMDVerificationJob(payload)
    } catch (error) {
      if (error instanceof Error && error.message.includes('429')) {
        if (attempt === maxRetries - 1) throw error
        
        console.log(`Rate limited, retrying in ${delay}ms...`)
        await new Promise(resolve => setTimeout(resolve, delay))
        delay *= 2 // Exponential backoff
      } else {
        throw error // Re-throw non-rate-limit errors
      }
    }
  }
}
```

## Real-Time Updates

For real-time status updates, poll the status endpoint:

```typescript
async function monitorJob(jobId: string, onUpdate: (status: JobStatusResponse) => void) {
  const pollInterval = 5000 // 5 seconds
  
  const poll = async () => {
    try {
      const status = await getMDVerificationJobStatus(jobId)
      onUpdate(status)
      
      // Stop polling when job completes
      if (status.status === 'completed' || status.status === 'failed') {
        return
      }
      
      setTimeout(poll, pollInterval)
    } catch (error) {
      console.error('Polling error:', error)
      setTimeout(poll, pollInterval)
    }
  }
  
  poll()
}

// Usage
monitorJob('job-abc-123', (status) => {
  console.log(`Job status: ${status.status}`)
  if (status.status === 'completed') {
    console.log('Job completed successfully!')
  }
})
```

## TypeScript Types

All TypeScript types are exported from `@/lib/md-verification-api`:

```typescript
import {
  // Enums
  JobStatus,
  HpcJobStatus,
  DefectType,
  FittingMethod,
  
  // Request/Response types
  MDVerificationJobSubmitRequest,
  MDVerificationJobResponse,
  MDVerificationJobListResponse,
  JobStatusResponse,
  MDSimulationResultResponse,
  DefectAnalysisResultResponse,
  PotentialFittingResultResponse,
  
  // API functions
  submitMDVerificationJob,
  listMDVerificationJobs,
  getMDVerificationJob,
  getMDVerificationJobStatus,
  cancelMDVerificationJob,
  getSimulationResults,
  getDefectAnalysisResults,
  getFittingResults
} from '@/lib/md-verification-api'
```

## Testing

Mock the API client for testing:

```typescript
import { submitMDVerificationJob } from '@/lib/md-verification-api'

// Mock successful response
vi.mock('@/lib/md-verification-api', () => ({
  submitMDVerificationJob: vi.fn().mockResolvedValue({
    id: 'test-job-123',
    potential_id: 'EAM_alloy_U',
    element_system: 'U',
    status: 'pending',
    // ... other fields
  })
}))
```

## Best Practices

1. **Authentication**: Always include valid JWT tokens
2. **Error Handling**: Implement comprehensive error handling
3. **Rate Limiting**: Respect rate limits with backoff
4. **Type Safety**: Use TypeScript types for all API calls
5. **Polling**: Use reasonable intervals (5-10 seconds) for status checks
6. **Cleanup**: Cancel polling when components unmount

## Support

For API issues or questions:
- Check the [User Guide](./user-guide-md-verification.md)
- Review backend API documentation
- Check server logs for detailed error messages
- Contact the NFMD development team
