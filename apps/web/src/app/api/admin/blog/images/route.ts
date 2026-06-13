import { NextRequest, NextResponse } from "next/server"
import { writeFile, mkdir } from "node:fs/promises"
import { existsSync } from "node:fs"
import path from "node:path"
import { createReadStream } from "node:fs"

// Allowed image file types
const ALLOWED_TYPES = [
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/gif",
  "image/webp",
  "image/svg+xml",
]

// Allowed file extensions
const ALLOWED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]

// Maximum file size: 5MB
const MAX_FILE_SIZE = 5 * 1024 * 1024

// Minimum file size: 100 bytes (prevent empty files)
const MIN_FILE_SIZE = 100

function getImagesDir(): string {
  return process.env.BLOG_IMAGES_DIR || path.join(process.cwd(), "public", "blog", "images")
}

function validateFileType(fileType: string): boolean {
  return ALLOWED_TYPES.includes(fileType)
}

function validateFileExtension(filename: string): boolean {
  const ext = path.extname(filename).toLowerCase()
  return ALLOWED_EXTENSIONS.includes(ext)
}

function validateFileSignature(buffer: Buffer, fileType: string): boolean {
  // Check file signatures (magic numbers)
  const header = buffer.subarray(0, 12)

  switch (fileType) {
    case "image/jpeg":
    case "image/jpg":
      // JPEG: FF D8 FF
      return header[0] === 0xff && header[1] === 0xd8 && header[2] === 0xff
    case "image/png":
      // PNG: 89 50 4E 47 0D 0A 1A 0A
      return (
        header[0] === 0x89 &&
        header[1] === 0x50 &&
        header[2] === 0x4e &&
        header[3] === 0x47 &&
        header[4] === 0x0d &&
        header[5] === 0x0a &&
        header[6] === 0x1a &&
        header[7] === 0x0a
      )
    case "image/gif":
      // GIF: 47 49 46 38 (GIF8)
      return (
        header[0] === 0x47 &&
        header[1] === 0x49 &&
        header[2] === 0x46 &&
        header[3] === 0x38
      )
    case "image/webp":
      // WebP: RIFF....WEBP
      return (
        header[0] === 0x52 &&
        header[1] === 0x49 &&
        header[2] === 0x46 &&
        header[3] === 0x46 &&
        header[8] === 0x57 &&
        header[9] === 0x45 &&
        header[10] === 0x42 &&
        header[11] === 0x50
      )
    case "image/svg+xml":
      // SVG: Check for XML declaration or <svg tag
      const content = buffer.toString("utf-8", 0, Math.min(buffer.length, 100))
      return (
        content.includes("<?xml") ||
        content.includes("<svg") ||
        content.includes("<?SVG")
      )
    default:
      return false
  }
}

function sanitizeFilename(filename: string): string {
  // Remove any directory traversal attempts and special characters
  return filename
    .replace(/[^a-zA-Z0-9._-]/g, "_")
    .replace(/\.{2,}/g, ".")
    .toLowerCase()
}

function generateUniqueFilename(originalName: string): string {
  const timestamp = Date.now()
  const random = Math.random().toString(36).substring(2, 8)
  const ext = path.extname(originalName).toLowerCase()
  const baseName = sanitizeFilename(path.basename(originalName, ext))
  const truncatedName = baseName.substring(0, 32) // Limit base name length
  return `${truncatedName}-${timestamp}-${random}${ext}`
}

async function validateImageContent(filePath: string): Promise<boolean> {
  return new Promise((resolve) => {
    const stream = createReadStream(filePath)
    let buffer = Buffer.alloc(0)

    stream.on("data", (chunk: Buffer) => {
      buffer = Buffer.concat([buffer, chunk])
      if (buffer.length >= 12) {
        stream.destroy()
        resolve(true) // If we can read it, it's valid
      }
    })

    stream.on("error", () => {
      resolve(false)
    })

    stream.on("end", () => {
      resolve(buffer.length >= 12)
    })
  })
}

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData()
    const file = formData.get("file") as File

    if (!file) {
      return NextResponse.json(
        { success: false, error: "No file provided" },
        { status: 400 }
      )
    }

    // Validate file size
    if (file.size < MIN_FILE_SIZE) {
      return NextResponse.json(
        { success: false, error: "File is too small or empty" },
        { status: 400 }
      )
    }

    if (file.size > MAX_FILE_SIZE) {
      return NextResponse.json(
        { success: false, error: `File size exceeds ${MAX_FILE_SIZE / 1024 / 1024}MB limit` },
        { status: 400 }
      )
    }

    // Validate file extension
    if (!validateFileExtension(file.name)) {
      return NextResponse.json(
        { success: false, error: "Invalid file extension. Only .jpg, .jpeg, .png, .gif, .webp, and .svg are allowed." },
        { status: 400 }
      )
    }

    // Validate file type
    if (!validateFileType(file.type)) {
      return NextResponse.json(
        { success: false, error: "Invalid file type. Only JPEG, PNG, GIF, WebP, and SVG are allowed." },
        { status: 400 }
      )
    }

    // Get file buffer for signature validation
    const bytes = await file.arrayBuffer()
    const buffer = Buffer.from(bytes)

    // Validate file signature (prevent file type spoofing)
    if (!validateFileSignature(buffer, file.type)) {
      return NextResponse.json(
        { success: false, error: "File content does not match the declared file type" },
        { status: 400 }
      )
    }

    // Generate unique filename
    const filename = generateUniqueFilename(file.name)

    // Ensure images directory exists
    const imagesDir = getImagesDir()
    if (!existsSync(imagesDir)) {
      await mkdir(imagesDir, { recursive: true })
    }

    // Save file
    const filePath = path.join(imagesDir, filename)
    await writeFile(filePath, buffer)

    // Validate saved file
    const isValidImage = await validateImageContent(filePath)
    if (!isValidImage) {
      return NextResponse.json(
        { success: false, error: "Failed to validate uploaded image" },
        { status: 500 }
      )
    }

    // Return public URL
    const publicUrl = `/blog/images/${filename}`

    return NextResponse.json({
      success: true,
      data: {
        url: publicUrl,
        filename,
        size: file.size,
        type: file.type,
      },
    })
  } catch (error) {
    console.error("Error uploading image:", error)
    return NextResponse.json(
      { success: false, error: "Failed to upload image" },
      { status: 500 }
    )
  }
}
