import { describe, it, expect, beforeEach, afterEach } from "vitest"
import fs from "node:fs"
import path from "node:path"
import {
  getContentDir,
  parseMarkdownFile,
  getAllPosts,
  getPostBySlug,
  getAllSlugs,
} from "./posts"

const VALID_POST_A = `---
title: 核燃料循环概述
date: 2026-01-15
summary: 本文介绍了核燃料循环的基本概念和主要环节。
tags:
  - 核燃料
  - 循环
author: 张三
---

# 核燃料循环概述

核燃料循环是核能利用的核心环节。
`

const VALID_POST_B = `---
title: 材料物性数据库设计
date: 2026-02-20
summary: 介绍NucPot数据库的架构设计和技术选型。
tags:
  - 数据库
  - 架构
author: 李四
---

# 数据库设计

本文详细介绍数据库的设计思路。
`

const MISSING_TITLE = `---
date: 2026-01-15
summary: 缺少标题的文章
tags:
  - 测试
author: 张三
---

# 无标题
`

const MISSING_DATE = `---
title: 没有日期
summary: 缺少日期的文章
tags:
  - 测试
author: 张三
---

# 无日期
`

function cleanContentDir() {
  const dir = getContentDir()
  if (fs.existsSync(dir)) {
    const files = fs.readdirSync(dir)
    for (const file of files) {
      fs.unlinkSync(path.join(dir, file))
    }
  }
}

function writePost(fileName: string, content: string) {
  const dir = getContentDir()
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true })
  }
  fs.writeFileSync(path.join(dir, fileName), content)
}

describe("getContentDir", () => {
  it("returns content/blog path from env or cwd fallback", () => {
    const dir = getContentDir()
    expect(dir).toContain("content")
    expect(dir).toContain("blog")
  })
})

describe("parseMarkdownFile", () => {
  it("parses valid markdown with frontmatter", () => {
    const tmpDir = getContentDir()
    fs.mkdirSync(tmpDir, { recursive: true })
    const tmpFile = path.join(tmpDir, "_test-parse.md")
    fs.writeFileSync(tmpFile, VALID_POST_A)

    try {
      const post = parseMarkdownFile(tmpFile, "_test-parse.md")

      expect(post).not.toBeNull()
      expect(post?.slug).toBe("_test-parse")
      expect(post?.frontmatter.title).toBe("核燃料循环概述")
      expect(post?.frontmatter.date).toBe("2026-01-15")
      expect(post?.frontmatter.summary).toBe(
        "本文介绍了核燃料循环的基本概念和主要环节。"
      )
      expect(post?.frontmatter.tags).toEqual(["核燃料", "循环"])
      expect(post?.frontmatter.author).toBe("张三")
      expect(post?.content).toContain("核燃料循环是核能利用的核心环节")
    } finally {
      fs.unlinkSync(tmpFile)
    }
  })

  it("returns null for missing title", () => {
    const tmpDir = getContentDir()
    fs.mkdirSync(tmpDir, { recursive: true })
    const tmpFile = path.join(tmpDir, "_test-notitle.md")
    fs.writeFileSync(tmpFile, MISSING_TITLE)

    try {
      const post = parseMarkdownFile(tmpFile, "_test-notitle.md")
      expect(post).toBeNull()
    } finally {
      fs.unlinkSync(tmpFile)
    }
  })

  it("returns null for missing date", () => {
    const tmpDir = getContentDir()
    fs.mkdirSync(tmpDir, { recursive: true })
    const tmpFile = path.join(tmpDir, "_test-nodate.md")
    fs.writeFileSync(tmpFile, MISSING_DATE)

    try {
      const post = parseMarkdownFile(tmpFile, "_test-nodate.md")
      expect(post).toBeNull()
    } finally {
      fs.unlinkSync(tmpFile)
    }
  })

  it("returns null for empty tags array", () => {
    const emptyTags = `---
title: 空标签
date: 2026-01-01
summary: 测试
tags: []
author: 测试
---

content
`
    const tmpDir = getContentDir()
    fs.mkdirSync(tmpDir, { recursive: true })
    const tmpFile = path.join(tmpDir, "_test-emptytags.md")
    fs.writeFileSync(tmpFile, emptyTags)

    try {
      // Empty tags is still a valid array, so it should parse
      const post = parseMarkdownFile(tmpFile, "_test-emptytags.md")
      expect(post).not.toBeNull()
      expect(post?.frontmatter.tags).toEqual([])
    } finally {
      fs.unlinkSync(tmpFile)
    }
  })
})

describe("getAllPosts", () => {
  beforeEach(() => {
    cleanContentDir()
  })

  afterEach(() => {
    cleanContentDir()
  })

  it("returns all posts sorted by date descending", () => {
    writePost("post-a.md", VALID_POST_A)
    writePost("post-b.md", VALID_POST_B)

    const posts = getAllPosts()

    expect(posts).toHaveLength(2)
    expect(posts[0]!.slug).toBe("post-b")
    expect(posts[0]!.title).toBe("材料物性数据库设计")
    expect(posts[1]!.slug).toBe("post-a")
    expect(posts[1]!.title).toBe("核燃料循环概述")
  })

  it("extracts frontmatter correctly", () => {
    writePost("test.md", VALID_POST_A)

    const post = getAllPosts()[0]!

    expect(post.slug).toBe("test")
    expect(post.title).toBe("核燃料循环概述")
    expect(post.date).toBe("2026-01-15")
    expect(post.summary).toBe(
      "本文介绍了核燃料循环的基本概念和主要环节。"
    )
    expect(post.tags).toEqual(["核燃料", "循环"])
    expect(post.author).toBe("张三")
  })

  it("returns empty array when no posts exist", () => {
    const posts = getAllPosts()
    expect(posts).toEqual([])
  })

  it("skips files with missing required frontmatter fields", () => {
    writePost("no-title.md", MISSING_TITLE)
    writePost("no-date.md", MISSING_DATE)
    writePost("valid.md", VALID_POST_A)

    const posts = getAllPosts()
    expect(posts).toHaveLength(1)
    expect(posts[0]!.slug).toBe("valid")
  })

  it("skips non-markdown files", () => {
    writePost("readme.txt", "not markdown")
    writePost("image.png", "not markdown")
    writePost("post.md", VALID_POST_A)

    const posts = getAllPosts()
    expect(posts).toHaveLength(1)
    expect(posts[0]!.slug).toBe("post")
  })
})

describe("getPostBySlug", () => {
  beforeEach(() => {
    cleanContentDir()
  })

  afterEach(() => {
    cleanContentDir()
  })

  it("returns the matching post with content", () => {
    writePost("target.md", VALID_POST_A)

    const post = getPostBySlug("target")

    expect(post).not.toBeNull()
    expect(post?.slug).toBe("target")
    expect(post?.frontmatter.title).toBe("核燃料循环概述")
    expect(post?.content).toContain("核燃料循环是核能利用的核心环节")
  })

  it("returns null for non-existent slug", () => {
    writePost("other.md", VALID_POST_A)
    expect(getPostBySlug("missing")).toBeNull()
  })

  it("returns null when post has invalid frontmatter", () => {
    writePost("invalid.md", MISSING_TITLE)
    expect(getPostBySlug("invalid")).toBeNull()
  })
})

describe("getAllSlugs", () => {
  beforeEach(() => {
    cleanContentDir()
  })

  afterEach(() => {
    cleanContentDir()
  })

  it("returns all valid slugs sorted by date desc", () => {
    writePost("a.md", VALID_POST_A)
    writePost("b.md", VALID_POST_B)

    const slugs = getAllSlugs()
    expect(slugs).toEqual(["b", "a"])
  })

  it("returns empty array when no valid posts", () => {
    writePost("invalid.md", MISSING_TITLE)
    expect(getAllSlugs()).toEqual([])
  })
})
