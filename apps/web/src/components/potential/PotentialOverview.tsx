"use client"

import { Descriptions, Tag, Space, Typography } from "antd"
import type { PotentialDetail } from "@/lib/potentials-api"

const { Paragraph, Text } = Typography

const TYPE_COLOR: Record<string, string> = {
  EAM: "blue",
  MEAM: "green",
  MTP: "purple",
  ACE: "orange",
}

const STATUS_LABELS: Record<string, string> = {
  unverified: "未验证",
  pending: "验证中",
  verified: "已验证",
  failed: "验证失败",
}

const STATUS_COLORS: Record<string, string> = {
  unverified: "default",
  pending: "processing",
  verified: "success",
  failed: "error",
}

/** Unified placeholder for absent optional values (「未提供」semantics, rendered as —). */
const PLACEHOLDER = "—"

interface PotentialOverviewProps {
  readonly detail: PotentialDetail
}

interface ApplicabilityShape {
  readonly temperatureRange?: unknown
  readonly phases?: unknown
  readonly notes?: unknown
}

interface ReferenceShape {
  readonly doi?: unknown
  readonly citation?: unknown
}

interface DeveloperShape {
  readonly name?: unknown
  readonly affiliation?: unknown
}

function typeColor(type: string): string {
  return TYPE_COLOR[type] ?? "default"
}

function asStringArray(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return []
  return value.filter((v): v is string => typeof v === "string")
}

function asNumberArray(value: unknown): readonly number[] {
  if (!Array.isArray(value)) return []
  return value.filter((v): v is number => typeof v === "number")
}

function asString(value: unknown): string {
  if (value == null) return ""
  if (typeof value === "string") return value
  return String(value)
}

function asObjectArray(value: unknown): readonly Record<string, unknown>[] {
  if (!Array.isArray(value)) return []
  return value.filter(
    (v): v is Record<string, unknown> => typeof v === "object" && v !== null,
  )
}

/** Formats an applicability temperature range as e.g. 「300–5000 K」. */
function formatTemperatureRange(applicability: unknown): string {
  const shape = applicability as ApplicabilityShape | undefined
  const range = asNumberArray(shape?.temperatureRange)
  if (range.length !== 2) return ""
  return `${range[0]}–${range[1]} K`
}

export function PotentialOverview({ detail }: PotentialOverviewProps) {
  const {
    name,
    type,
    format,
    elements,
    system_name,
    description,
    source,
    source_doi,
    version,
    tags,
    references,
    verification_status,
    license,
    applicability,
    developers,
    sim_software,
  } = detail

  // Empty/missing verification status is treated as unverified (NFM-4314: the
  // badge used to render blank when the API returned null).
  const status = verification_status || "unverified"

  const doi = source_doi ?? asString(asObjectArray(references)[0]?.doi)

  const applicabilityShape = applicability as ApplicabilityShape | undefined
  const temperatureRange = formatTemperatureRange(applicability)
  const phases = asStringArray(applicabilityShape?.phases)
  const applicabilityNotes = asString(applicabilityShape?.notes).trim()

  const referenceItems = asObjectArray(references)
  const developerItems = asObjectArray(developers)
  const simSoftware = asStringArray(sim_software)

  return (
    <Descriptions
      bordered
      column={2}
      size="small"
      labelStyle={{ width: 120, fontWeight: 600 }}
    >
      <Descriptions.Item label="名称">{name}</Descriptions.Item>
      <Descriptions.Item label="类型">
        <Tag color={typeColor(type)}>{type}</Tag>
      </Descriptions.Item>
      <Descriptions.Item label="验证状态" span={2}>
        <Tag color={STATUS_COLORS[status] ?? "default"}>
          {STATUS_LABELS[status] ?? status}
        </Tag>
      </Descriptions.Item>
      <Descriptions.Item label="格式">{format || PLACEHOLDER}</Descriptions.Item>
      <Descriptions.Item label="版本">{version || PLACEHOLDER}</Descriptions.Item>
      <Descriptions.Item label="许可证">{license || PLACEHOLDER}</Descriptions.Item>
      <Descriptions.Item label="模拟软件">
        <Space wrap size={[0, 4]}>
          {simSoftware.length > 0 ? (
            simSoftware.map((s) => <Tag key={s}>{s}</Tag>)
          ) : (
            <span>{PLACEHOLDER}</span>
          )}
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="元素" span={2}>
        <Space wrap size={[0, 4]}>
          {elements.length > 0 ? (
            elements.map((el) => <Tag key={el}>{el}</Tag>)
          ) : (
            <span>{PLACEHOLDER}</span>
          )}
        </Space>
      </Descriptions.Item>
      <Descriptions.Item label="体系">{system_name || PLACEHOLDER}</Descriptions.Item>
      <Descriptions.Item label="来源">{source || PLACEHOLDER}</Descriptions.Item>
      <Descriptions.Item label="温度范围">
        {temperatureRange || PLACEHOLDER}
      </Descriptions.Item>
      <Descriptions.Item label="相态">
        {phases.length > 0 ? phases.join("、") : PLACEHOLDER}
      </Descriptions.Item>
      <Descriptions.Item label="DOI" span={2}>
        {doi ? (
          <a
            href={`https://doi.org/${doi}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {doi}
          </a>
        ) : (
          PLACEHOLDER
        )}
      </Descriptions.Item>
      <Descriptions.Item label="文献引用" span={2}>
        {referenceItems.length > 0 ? (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            {referenceItems.map((ref, i) => {
              const shape = ref as ReferenceShape
              const refDoi = asString(shape.doi).trim()
              const citation = asString(shape.citation).trim()
              return (
                <Paragraph key={`${refDoi}-${i}`} style={{ marginBottom: 0 }}>
                  {citation || PLACEHOLDER}
                  {refDoi && (
                    <>
                      {" "}
                      <a
                        href={`https://doi.org/${refDoi}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        (DOI: {refDoi})
                      </a>
                    </>
                  )}
                </Paragraph>
              )
            })}
          </Space>
        ) : (
          PLACEHOLDER
        )}
      </Descriptions.Item>
      <Descriptions.Item label="开发者" span={2}>
        {developerItems.length > 0 ? (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            {developerItems.map((dev, i) => {
              const shape = dev as DeveloperShape
              const devName = asString(shape.name).trim()
              const affiliation = asString(shape.affiliation).trim()
              return (
                <Text key={`${devName}-${i}`}>
                  {devName || PLACEHOLDER}
                  {affiliation && `（${affiliation}）`}
                </Text>
              )
            })}
          </Space>
        ) : (
          PLACEHOLDER
        )}
      </Descriptions.Item>
      <Descriptions.Item label="适用性备注" span={2}>
        {applicabilityNotes ? (
          <Paragraph style={{ marginBottom: 0 }}>{applicabilityNotes}</Paragraph>
        ) : (
          PLACEHOLDER
        )}
      </Descriptions.Item>
      <Descriptions.Item label="描述" span={2}>
        <Paragraph style={{ marginBottom: 0 }}>
          {description || PLACEHOLDER}
        </Paragraph>
      </Descriptions.Item>
      <Descriptions.Item label="标签" span={2}>
        <Space wrap size={[0, 4]}>
          {asStringArray(tags).length > 0 ? (
            asStringArray(tags).map((t) => (
              <Tag key={t} color="default">
                {t}
              </Tag>
            ))
          ) : (
            <span>{PLACEHOLDER}</span>
          )}
        </Space>
      </Descriptions.Item>
    </Descriptions>
  )
}
