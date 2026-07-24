import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DocsShell } from "@/components/docs-shell";
import { docPages } from "@/lib/content";
import { allNavItems } from "@/lib/navigation";

const slugAliases: Record<string, string> = {
  quickstart: "isaac-sim-quickstart",
};

const canonicalSlug = (slug: string) => slugAliases[slug] ?? slug;

export function generateStaticParams() {
  return [...allNavItems.map((item) => ({ slug: item.slug })), { slug: "quickstart" }];
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const page = docPages[canonicalSlug(slug)];
  return page ? { title: page.title, description: page.description } : {};
}

export default async function DocumentationPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const activeSlug = canonicalSlug(slug);
  const page = docPages[activeSlug];
  if (!page) notFound();

  return (
    <DocsShell activeSlug={activeSlug}>
      <article className="doc-article">
        <header className="article-header">
          <div className="eyebrow">{page.eyebrow}</div>
          <h1>{page.title}</h1>
          <p>{page.description}</p>
        </header>
        <div className="article-body">{page.body}</div>
        <footer className="article-footer">
          <span>JenAI v2.4.0</span>
          <span>Evidence-aware · ROS 2 Jazzy · Isaac Sim 5.1</span>
        </footer>
      </article>
    </DocsShell>
  );
}
