"use client"

import { useCallback, useState } from "react"
import { FloatButton } from "antd"
import { CommentOutlined } from "@ant-design/icons"
import { FeedbackModal } from "./FeedbackModal"

export function FeedbackFloatButton() {
  const [modalOpen, setModalOpen] = useState(false)

  const handleOpen = useCallback(() => {
    setModalOpen(true)
  }, [])

  const handleClose = useCallback(() => {
    setModalOpen(false)
  }, [])

  return (
    <>
      <FloatButton
        icon={<CommentOutlined />}
        type="primary"
        tooltip="意见反馈"
        onClick={handleOpen}
        style={{ right: 24, bottom: 80 }}
      />
      <FeedbackModal open={modalOpen} onClose={handleClose} />
    </>
  )
}
