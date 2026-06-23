"use client"

import { useState, useCallback } from "react"
import {
  Steps,
  Button,
  Modal,
  message,
  Divider,
  Flex,
} from "antd"
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  PlusOutlined,
} from "@ant-design/icons"
import {
  WIZARD_STEP_TITLES,
  INITIAL_WIZARD_FORM_DATA,
  type WizardFormData,
  type WizardStepIndex,
} from "./wizard/wizard-types"
import { PotentialSelectorStep } from "./wizard/potential-selector-step"
import { SimulationConfigStep } from "./wizard/simulation-config-step"
import { ConfirmationStep } from "./wizard/confirmation-step"

interface TaskSubmissionWizardProps {
  onSuccess?: (jobId: string) => void
}

/**
 * 3-step guided wizard for creating MD verification jobs.
 *
 * Renders a trigger button that opens a Modal containing the wizard.
 *
 * Step 0 – Select a potential from the library
 * Step 1 – Configure simulation parameters & HPC backend
 * Step 2 – Review summary, confirm, and submit
 */
export function TaskSubmissionWizard({ onSuccess }: TaskSubmissionWizardProps) {
  const [modalOpen, setModalOpen] = useState(false)
  const [currentStep, setCurrentStep] = useState<WizardStepIndex>(0)
  const [formData, setFormData] = useState<WizardFormData>({
    ...INITIAL_WIZARD_FORM_DATA,
  })

  const updateField = useCallback(
    (field: keyof WizardFormData, value: unknown) => {
      setFormData((prev) => ({ ...prev, [field]: value }))
    },
    [],
  )

  const validateCurrentStep = (): boolean => {
    switch (currentStep) {
      case 0:
        if (!formData.selectedPotential) {
          message.warning("请先选择一个势函数")
          return false
        }
        return true

      case 1:
        if (!formData.structureFile.trim()) {
          message.warning("请输入结构文件路径")
          return false
        }
        return true

      case 2:
        return true
    }
  }

  const handleNext = () => {
    if (!validateCurrentStep()) return
    if (currentStep < 2) {
      setCurrentStep((prev) => (prev + 1) as WizardStepIndex)
    }
  }

  const handlePrev = () => {
    if (currentStep > 0) {
      setCurrentStep((prev) => (prev - 1) as WizardStepIndex)
    }
  }

  const handleSuccess = (jobId: string) => {
    setFormData({ ...INITIAL_WIZARD_FORM_DATA })
    setCurrentStep(0)
    setModalOpen(false)
    onSuccess?.(jobId)
  }

  const handleModalOpen = () => {
    setFormData({ ...INITIAL_WIZARD_FORM_DATA })
    setCurrentStep(0)
    setModalOpen(true)
  }

  const handleModalClose = () => {
    setModalOpen(false)
  }

  const renderCurrentStep = () => {
    switch (currentStep) {
      case 0:
        return (
          <PotentialSelectorStep
            formData={formData}
            onUpdateField={updateField}
          />
        )

      case 1:
        return (
          <SimulationConfigStep
            formData={formData}
            onUpdateField={updateField}
          />
        )

      case 2:
        return (
          <ConfirmationStep
            formData={formData}
            onSuccess={handleSuccess}
            onPrev={handlePrev}
          />
        )
    }
  }

  return (
    <>
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={handleModalOpen}
        size="large"
      >
        创建验证任务
      </Button>

      <Modal
        title="创建 MD 验证任务"
        open={modalOpen}
        onCancel={handleModalClose}
        footer={null}
        width={900}
        centered
        closable={false}
        className="wizard-modal"
        destroyOnClose
      >
        {/* Step progress indicator */}
        <Steps
          current={currentStep}
          items={WIZARD_STEP_TITLES.map((step) => ({
            title: step.title,
            description: step.description,
          }))}
          style={{ marginBottom: 24 }}
          size="small"
        />

        <Divider style={{ margin: "0 0 24px" }} />

        {/* Current step content */}
        {renderCurrentStep()}

        {/* Navigation buttons – hidden on final step (submit is inside ConfirmationStep) */}
        {currentStep < 2 && (
          <div style={{ marginTop: 24 }}>
            <Flex justify="flex-end" gap="small">
              {currentStep > 0 && (
                <Button icon={<ArrowLeftOutlined />} onClick={handlePrev}>
                  上一步
                </Button>
              )}
              <Button
                type="primary"
                icon={<ArrowRightOutlined />}
                onClick={handleNext}
              >
                下一步
              </Button>
            </Flex>
          </div>
        )}
      </Modal>
    </>
  )
}
