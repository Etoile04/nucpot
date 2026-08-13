"use client"

import { useCallback, useState } from "react"
import { usePathname } from "next/navigation"
import { FloatButton } from "antd"
import { CommentOutlined } from "@ant-design/icons"
import { FeedbackModal } from "./FeedbackModal"

/** Paths where the FloatButton is hidden to prevent overlapping embedded content. */
const HIDE_PATHS = ["/ontology"] as const

export function FeedbackFloatButton() {
  const [modalOpen, setModalOpen] = useState(false)
  const pathname = usePathname()

  const handleOpen = useCallback(() => {
    setModalOpen(true)
  }, [])

  const handleClose = useCallback(() => {
    setModalOpen(false)
  }, [])

  // Hide on pages with embedded viewers (e.g. ontology iframe) to prevent overlap.
  // Feedback is still accessible via the footer mailto link.
  const isHidden = HIDE_PATHS.some((p) => pathname.startsWith(p))

  if (isHidden) {
    return null
  }

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
