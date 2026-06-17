import { Metadata } from 'next';
import OntologyViewerFrame from '@/components/ontology/OntologyViewerFrame';

export const metadata: Metadata = {
  title: '本体可视化 - NFMD',
  description: '核燃料材料本体可视化浏览器',
};

interface OntologyPageProps {
  searchParams: Promise<{ node?: string }>;
}

export default async function OntologyPage({ searchParams }: OntologyPageProps) {
  const { node } = await searchParams;
  // Boundary validation: node only becomes an encoded ?node= deep-link value
  // into our same-origin viewer; cap length to bound the iframe URL.
  const safeNode = node && node.length <= 200 ? node : undefined;
  return (
    <div className="ontology-page">
      <OntologyViewerFrame node={safeNode} />
    </div>
  );
}
