import { MetadataRoute } from 'next'

interface PotentialItem {
  id: string
  updated_at?: string
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const baseUrl = 'https://nucpot.vercel.app'

  const staticPages: MetadataRoute.Sitemap = [
    { url: baseUrl, lastModified: new Date(), changeFrequency: 'weekly', priority: 1.0 },
    { url: `${baseUrl}/browse`, lastModified: new Date(), changeFrequency: 'daily', priority: 0.9 },
    { url: `${baseUrl}/search`, lastModified: new Date(), changeFrequency: 'weekly', priority: 0.8 },
    { url: `${baseUrl}/about`, lastModified: new Date(), changeFrequency: 'monthly', priority: 0.5 },
    { url: `${baseUrl}/upload`, lastModified: new Date(), changeFrequency: 'monthly', priority: 0.5 },
    { url: `${baseUrl}/compare`, lastModified: new Date(), changeFrequency: 'weekly', priority: 0.7 },
  ]

  try {
    const res = await fetch(`${baseUrl}/api/potentials?limit=100`, {
      next: { revalidate: 3600 },
    })
    const data = await res.json()
    const potentials: PotentialItem[] = data.potentials || []

    const dynamicPages: MetadataRoute.Sitemap = potentials.map((p) => ({
      url: `${baseUrl}/potential/${p.id}`,
      lastModified: p.updated_at ? new Date(p.updated_at) : new Date(),
      changeFrequency: 'monthly' as const,
      priority: 0.8,
    }))

    return [...staticPages, ...dynamicPages]
  } catch {
    return staticPages
  }
}
