"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { allNavItems, navGroups } from "@/lib/navigation";
import { PRODUCT_VERSION } from "@/lib/product";

export function DocsShell({
  activeSlug,
  children,
}: {
  activeSlug?: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [activeResult, setActiveResult] = useState(-1);
  const [menuOpen, setMenuOpen] = useState(false);
  const normalizedQuery = query.trim().toLowerCase();
  const results = useMemo(() => {
    if (!normalizedQuery) return [];
    return allNavItems
      .filter((item) =>
        [item.title, item.description, ...item.keywords]
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery),
      )
      .slice(0, 8);
  }, [normalizedQuery]);

  useEffect(() => {
    setActiveResult(results.length > 0 ? 0 : -1);
  }, [results]);

  const searchOpen = normalizedQuery.length > 0;
  const activeDescendant =
    activeResult >= 0 && results[activeResult]
      ? `documentation-search-result-${results[activeResult].slug}`
      : undefined;

  function closeSearch() {
    setQuery("");
    setActiveResult(-1);
  }

  function openResult(index: number) {
    const result = results[index];
    if (!result) return;
    closeSearch();
    router.push(`/docs/${result.slug}`);
  }

  return (
    <div className="site-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <header className="topbar">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">
            J
          </span>
          <span>JenAI Docs</span>
        </Link>
        <div className="top-actions">
          <div className="search-wrap">
            <span className="search-icon" aria-hidden="true">
              ⌕
            </span>
            <input
              role="combobox"
              aria-autocomplete="list"
              aria-controls="documentation-search-results"
              aria-expanded={searchOpen}
              aria-activedescendant={activeDescendant}
              aria-label="Search documentation"
              placeholder="Search documentation"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  closeSearch();
                  return;
                }
                if (!results.length) return;
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  setActiveResult((current) => (current + 1) % results.length);
                } else if (event.key === "ArrowUp") {
                  event.preventDefault();
                  setActiveResult(
                    (current) => (current - 1 + results.length) % results.length,
                  );
                } else if (event.key === "Enter") {
                  event.preventDefault();
                  openResult(activeResult >= 0 ? activeResult : 0);
                }
              }}
            />
            {searchOpen ? (
              <div
                className="search-results"
                id="documentation-search-results"
                role="listbox"
                aria-label="Search results"
              >
                {results.length > 0 ? (
                  results.map((item, index) => (
                    <Link
                      id={`documentation-search-result-${item.slug}`}
                      role="option"
                      aria-selected={activeResult === index}
                      className={activeResult === index ? "selected" : ""}
                      href={`/docs/${item.slug}`}
                      key={item.slug}
                      onMouseEnter={() => setActiveResult(index)}
                      onClick={closeSearch}
                    >
                      <strong>{item.title}</strong>
                      <span>{item.description}</span>
                    </Link>
                  ))
                ) : (
                  <p className="search-empty" role="status">
                    No matching documentation. Try a capability or ROS 2 term.
                  </p>
                )}
              </div>
            ) : null}
          </div>
          <span className="version-badge">v{PRODUCT_VERSION}</span>
          <button
            className="menu-button"
            type="button"
            aria-label={menuOpen ? "Close documentation menu" : "Open documentation menu"}
            aria-controls="documentation-navigation"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            ☰
          </button>
        </div>
      </header>

      <div className="docs-layout">
        <aside
          id="documentation-navigation"
          className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}
        >
          <nav aria-label="Documentation">
            {navGroups.map((group) => (
              <section className="nav-group" key={group.title}>
                <h2>{group.title}</h2>
                {group.items.map((item) => (
                  <Link
                    aria-current={activeSlug === item.slug ? "page" : undefined}
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
            <span className="dot-live" aria-hidden="true" />
            Reference platform · Isaac Sim 5.1
          </div>
        </aside>
        <main className="content" id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
