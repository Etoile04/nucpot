/**
 * useCreateVerificationTask — manages state for creating a LAMMPS verification
 * task from a Pareto recommendation composition.
 *
 * NFM-1676.2 (NFM-1751)
 */

import { useState, useCallback } from "react"
import { message } from "antd"
import { createVerificationTask } from "../api"
import type { VerificationTaskResponse } from "../types"

interface UseCreateVerificationTaskReturn {
  readonly loading: boolean
  readonly createdTask: VerificationTaskResponse | null
  readonly createTask: (compositionJson: string) => Promise<VerificationTaskResponse | null>
}

/**
 * Hook to create a verification task from a JSON-stringified composition.
 *
 * Parses the composition, calls the API with sensible defaults for
 * potential_function / temperature range / timestep count, and returns
 * the created task on success.
 */
export function useCreateVerificationTask(): UseCreateVerificationTaskReturn {
  const [loading, setLoading] = useState(false)
  const [createdTask, setCreatedTask] = useState<VerificationTaskResponse | null>(null)

  const createTask = useCallback(
    async (compositionJson: string): Promise<VerificationTaskResponse | null> => {
      let composition: Record<string, number>
      try {
        composition = JSON.parse(compositionJson) as Record<string, number>
      } catch {
        message.error("成分数据解析失败 / Failed to parse composition data")
        return null
      }

      setLoading(true)
      setCreatedTask(null)

      try {
        const result = await createVerificationTask({
          composition,
          potential_function: "EAM",
          temperature_min: 300,
          temperature_max: 1200,
          timestep_count: 10000,
        })

        const updatedTask = { ...result }
        setCreatedTask(updatedTask)

        message.success(
          `验证任务已创建 / Verification task created (ID: ${result.id.slice(0, 8)}…)`,
        )

        return updatedTask
      } catch (error: unknown) {
        const errorMessage =
          error instanceof Error ? error.message : "Unknown error"
        message.error(
          `创建验证任务失败 / Failed to create task: ${errorMessage}`,
        )
        return null
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  return { loading, createdTask, createTask } as const
}