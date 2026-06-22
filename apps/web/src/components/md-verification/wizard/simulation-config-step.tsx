"use client"

import {
  Form,
  InputNumber,
  Select,
  Slider,
  Input,
  Space,
  Card,
} from "antd"
import {
  ENSEMBLE_OPTIONS,
  HPC_BACKEND_OPTIONS,
  PHASE_OPTIONS,
  DEFECT_TYPE_OPTIONS,
} from "./wizard-types"
import type { WizardFormData } from "./wizard-types"

interface SimulationConfigStepProps {
  formData: WizardFormData
  onUpdateField: (field: keyof WizardFormData, value: unknown) => void
}

const TEMPERATURE_MARKS: Record<number, string> = {
  0: "0",
  300: "300",
  1000: "1k",
  5000: "5k",
  10000: "10k",
}

export function SimulationConfigStep({
  formData,
  onUpdateField,
}: SimulationConfigStepProps) {
  const update = (field: keyof WizardFormData, value: unknown) => {
    onUpdateField(field, value)
  }

  return (
    <Form layout="vertical">
      {/* Simulation Parameters */}
      <Card title="模拟参数" size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Form.Item label="温度 (K)" required>
            <Slider
              min={0}
              max={10000}
              value={formData.temperature}
              onChange={(v) => update("temperature", v)}
              marks={TEMPERATURE_MARKS}
            />
            <InputNumber
              min={0}
              max={10000}
              value={formData.temperature}
              onChange={(v) => update("temperature", v ?? 300)}
              addonAfter="K"
              style={{ marginTop: 8 }}
            />
          </Form.Item>

          <Form.Item label="压力 (GPa)" required>
            <InputNumber
              min={-100}
              max={100}
              step={0.1}
              value={formData.pressure}
              onChange={(v) => update("pressure", v ?? 0)}
              addonAfter="GPa"
              style={{ width: "100%" }}
            />
          </Form.Item>

          <Form.Item label="系综类型">
            <Select
              value={formData.ensemble}
              onChange={(v) => update("ensemble", v)}
              options={ENSEMBLE_OPTIONS}
            />
          </Form.Item>

          <Form.Item label="模拟时间 (ps)">
            <InputNumber
              min={1}
              max={10000}
              value={formData.simulationTime}
              onChange={(v) => update("simulationTime", v ?? 100)}
              addonAfter="ps"
              style={{ width: "100%" }}
            />
          </Form.Item>

          <Form.Item label="时间步长 (ps)">
            <InputNumber
              min={0.0001}
              max={0.01}
              step={0.0001}
              value={formData.timestep}
              onChange={(v) => update("timestep", v ?? 0.001)}
              addonAfter="ps"
              style={{ width: "100%" }}
            />
          </Form.Item>
        </Space>
      </Card>

      {/* Structure Configuration */}
      <Card title="结构配置" size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Form.Item label="相结构">
            <Select
              value={formData.phase}
              onChange={(v) => update("phase", v)}
              options={PHASE_OPTIONS}
            />
          </Form.Item>

          <Form.Item
            label="结构文件路径"
            required
            extra="服务器上的绝对路径，例如: /data/structures/BCC_U.cif"
          >
            <Input
              value={formData.structureFile}
              onChange={(e) => update("structureFile", e.target.value)}
              placeholder="/data/structures/BCC_U.cif"
            />
          </Form.Item>
        </Space>
      </Card>

      {/* Advanced Options */}
      <Card title="高级选项" size="small">
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Form.Item label="缺陷类型">
            <Select
              mode="multiple"
              value={formData.defectTypes}
              onChange={(v) => update("defectTypes", v)}
              options={DEFECT_TYPE_OPTIONS}
              placeholder="选择缺陷类型（可选）"
              allowClear
            />
          </Form.Item>

          <Form.Item label="HPC 计算后端">
            <Select
              value={formData.hpcBackend || undefined}
              onChange={(v) => update("hpcBackend", v)}
              options={HPC_BACKEND_OPTIONS}
              placeholder="选择计算后端"
              allowClear
            />
          </Form.Item>

          <Form.Item label="优先级" extra="1-10，数字越大优先级越高">
            <InputNumber
              min={1}
              max={10}
              value={formData.priority}
              onChange={(v) => update("priority", v ?? 5)}
              style={{ width: 160 }}
              addonAfter="/ 10"
            />
          </Form.Item>
        </Space>
      </Card>
    </Form>
  )
}
