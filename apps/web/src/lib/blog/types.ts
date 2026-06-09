export interface BlogPostFrontmatter {
  readonly title: string
  readonly date: string
  readonly summary: string
  readonly tags: readonly string[]
  readonly author: string
}

export interface BlogPost {
  readonly slug: string
  readonly frontmatter: BlogPostFrontmatter
  readonly content: string
}

export interface BlogPostMeta {
  readonly slug: string
  readonly title: string
  readonly date: string
  readonly summary: string
  readonly tags: readonly string[]
  readonly author: string
}
