"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { allNavItems, navGroups } from "@/lib/navigation";

export function DocsShell({
  activeSlug,
  children,
}: {
  activeSlug?: string;
  children: React.ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const results = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return [];
    return allNavItems
      .filter((item) =>
        [item.title, item.description, ...item.keywords]
          .join(" ")
          .toLowerCase()
          .includes(normalized),
      )
      .slice(0, 8);
  }, [query]);

  return (
    <div className="site-shell">
      <header className="topbar">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">J</span>
          <span>JenAI Docs</span>
        </Link>
        <div className="top-actions">
          <div className="search-wrap">
            <span className="search-icon" aria-hidden="true">⌕</span>
            <input
              aria-label="Search documentation"
              placeholder="Search documentation"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {results.length > 0 ? (
              <div className="search-results">
                {results.map((item) => (
                  <Link
                    href={`/docs/${item.slug}`}
                    key={item.slug}
                    onClick={() => setQuery("")}
                  >
                    <strong>{item.title}</strong>
                    <span>{item.description}</span>
                  </Link>
                ))}
              </div>
            ) : null}
          </div>
          <span className="version-badge">v2.2.0</span>
          <button
            className="menu-button"
            type="button"
            aria-label="Toggle documentation menu"
            onClick={() => setMenuOpen((open) => !open)}
          >
            ☰
          </button>
        </div>
      </header>

      <div className="docs-layout">
        <aside className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}>
          <nav aria-label="Documentation">
            {navGroups.map((group) => (
              <section className="nav-group" key={group.title}>
                <h2>{group.title}</h2>
                {group.items.map((item) => (
                  <Link
                    className={activeSlug === item.slug ? "active" : ""}
                    href={`/docs/${item.slug}`}
                    key={item.slug}
                    onClick={() => setMenuOpen(false)}
                  >
                    {item.title}
                  </Link>
                ))}
              </section>
            ))}
          </nav>
          <div className="sidebar-foot">
            <span className="dot-live" />
            Reference platform · Isaac Sim 5.1
          </div>
        </aside>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
