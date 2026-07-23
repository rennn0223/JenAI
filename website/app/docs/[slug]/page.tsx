import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DocsShell } from "@/components/docs-shell";
import { docPages } from "@/lib/content";
import { allNavItems } from "@/lib/navigation";

export function generateStaticParams() {
  return allNavItems.map((item) => ({ slug: item.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const page = docPages[slug];
  return page ? { title: page.title, description: page.description } : {};
}

export default async function DocumentationPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const page = docPages[slug];
  if (!page) notFound();

  return (
    <DocsShell activeSlug={slug}>
      <article className="doc-article">
        <header className="article-header">
          <div className="eyebrow">{page.eyebrow}</div>
          <h1>{page.title}</h1>
          <p>{page.description}</p>
        </header>
        <div className="article-body">{page.body}</div>
        <footer className="article-footer">
          <span>JenAI v2.2.0</span>
          <span>Evidence-aware · ROS 2 Jazzy · Isaac Sim 5.1</span>
        </footer>
      </article>
    </DocsShell>
  );
}
