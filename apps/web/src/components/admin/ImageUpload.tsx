"use client"

import { useState, useRef } from "react"

interface ImageUploadProps {
  onImageInsert: (markdown: string) => void
}

export default function ImageUpload({ onImageInsert }: ImageUploadProps) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setUploading(true)
    setError(null)

    try {
      const formData = new FormData()
      formData.append("file", file)

      const response = await fetch("/api/admin/blog/images", {
        method: "POST",
        body: formData,
      })

      const result = await response.json()

      if (!response.ok || !result.success) {
        throw new Error(result.error || "上传失败")
      }

      // Insert markdown image syntax
      const imageMarkdown = `![${file.name}](${result.data.url})`
      onImageInsert(imageMarkdown)

      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = ""
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败")
    } finally {
      setUploading(false)
    }
  }

  return (
    <div>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/jpg,image/png,image/gif,image/webp,image/svg+xml"
        onChange={handleFileSelect}
        disabled={uploading}
        style={{ display: "none" }}
      />
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        disabled={uploading}
        style={{
          padding: "0.5rem 1rem",
          fontSize: "0.875rem",
          fontWeight: 500,
          border: "1px dashed #d9d9d9",
          borderRadius: 4,
          background: uploading ? "#f5f5f5" : "#fff",
          color: uploading ? "#999" : "#1890ff",
          cursor: uploading ? "not-allowed" : "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: "0.5rem",
        }}
      >
        {uploading ? "上传中..." : "📷 上传图片"}
      </button>
      {error && (
        <div
          style={{
            marginTop: "0.5rem",
            padding: "0.5rem",
            background: "#fff2f0",
            border: "1px solid #ffccc7",
            borderRadius: 4,
            color: "#ff4d4f",
            fontSize: "0.875rem",
          }}
        >
          {error}
        </div>
      )}
    </div>
  )
}
