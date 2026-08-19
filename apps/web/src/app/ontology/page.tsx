import { Metadata } from 'next';
import OntologyCorpusBrowser from '@/components/ontology/OntologyCorpusBrowser';

export const metadata: Metadata = {
  title: '本体可视化 - NFMD',
  description: '核燃料材料本体可视化浏览器',
};

interface OntologyPageProps {
  searchParams: Promise<{ node?: string; corpus?: string }>;
}

export default async function OntologyPage({ searchParams }: OntologyPageProps) {
  const { node, corpus } = await searchParams;
  // Boundary validation: node/corpus only become encoded deep-link values into
  // our same-origin viewer / backend graph fetch; cap length to bound the URLs.
  const safeNode = node && node.length <= 200 ? node : undefined;
  const safeCorpus = corpus && corpus.length <= 200 ? corpus : undefined;
  return (
    <OntologyCorpusBrowser node={safeNode} corpus={safeCorpus} />
  );
}