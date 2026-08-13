"use client"

import { Card, Flex, Typography } from "antd"
import VerificationBadge from "@/components/VerificationBadge"

interface GradeData {
  overall?: string | null
  lattice?: string | null
  elastic?: string | null
  defect?: string | null
}

interface GradeBadgesProps {
  /** Grade data from verification results */
  grades: GradeData
  /** Optional CSS class for the wrapper */
  className?: string
}

const { Text } = Typography

/** Grade slot configuration: key maps to GradeData, label for display */
const GRADE_SLOTS: ReadonlyArray<{
  key: keyof GradeData
  label: string
  size: "lg" | "sm"
}> = [
  { key: "overall", label: "综合评级", size: "lg" },
  { key: "lattice", label: "晶格常数", size: "sm" },
  { key: "elastic", label: "弹性常数", size: "sm" },
  { key: "defect", label: "缺陷形成能", size: "sm" },
]

/**
 * GradeBadges — displays A-F verification grade badges
 * per the UX spec Section 3.1 (评级徽章区域).
 *
 * The overall grade uses the large variant; sub-category
 * grades use the small variant.
 */
export function GradeBadges({ grades, className }: GradeBadgesProps) {
  return (
    <Card
      title="验证评级"
      size="small"
      className={className}
      data-testid="grade-badges"
    >
      <Flex justify="center" gap="large" wrap="wrap">
        {GRADE_SLOTS.map(({ key, label, size }) => (
          <Flex key={key} vertical align="center" gap={4}>
            <VerificationBadge grade={grades[key]} size={size} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {label}
            </Text>
          </Flex>
        ))}
      </Flex>
    </Card>
  )
}
