import { Metadata } from 'next'

interface PotentialData {
  display_name?: string
  name: string
  description?: string
  elements: string[]
  type: string
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>
}): Promise<Metadata> {
  const { id } = await params

  try {
    const res = await fetch(`https://nucpot.vercel.app/api/potentials/${id}`, {
      next: { revalidate: 3600 },
    })
    const p: PotentialData = await res.json()

    const title = `${p.display_name || p.name} — NucPot`
    const desc = p.description
      ? `${p.description} ${p.type} 势函数`
      : `${p.elements.join('-')} ${p.type} 势函数`

    return {
      title,
      description: desc,
      openGraph: {
        title,
        description: desc,
        url: `https://nucpot.vercel.app/potential/${id}`,
      },
    }
  } catch {
    return {
      title: '势函数详情 — NucPot',
      description: '核材料势函数详情页',
    }
  }
}

export default function PotentialLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
