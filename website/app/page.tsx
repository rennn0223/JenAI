import Link from "next/link";

import { DocsShell } from "@/components/docs-shell";

const cards = [
  {
    kicker: "START HERE",
    title: "Isaac Sim QuickStart",
    text: "From a clean Play state to an approved, verified Nav2 task.",
    href: "/docs/isaac-sim-quickstart",
  },
  {
    kicker: "UNDERSTAND",
    title: "Decision boundary",
    text: "See exactly what the Agent decides and what remains in robot control.",
    href: "/docs/decision-boundary",
  },
  {
    kicker: "BUILD TRUST",
    title: "Task outcomes",
    text: "Read honest evidence contracts instead of a generic success message.",
    href: "/docs/task-outcomes",
  },
  {
    kicker: "DEPLOY",
    title: "Site Profiles",
    text: "Bind locations and docks to the exact ROS map where they are valid.",
    href: "/docs/site-profiles",
  },
  {
    kicker: "AUTONOMY",
    title: "Semantic area patrol",
    text: "Let one deterministic workflow cover configured areas, preserve evidence, and return home.",
    href: "/docs/area-patrol",
  },
];

export default function Home() {
  return (
    <DocsShell>
      <section className="home-hero">
        <div className="hero-copy">
          <div className="eyebrow">JENAI 2.4 · SIMULATION-FIRST ROBOTICS</div>
          <h1>A decision layer for robots—without replacing their controls.</h1>
          <p>
            Let the LLM select a bounded robot workflow, then execute it through registered ROS 2
            capabilities with deterministic evidence and honest outcomes.
          </p>
          <div className="hero-actions">
            <Link className="button button-primary" href="/docs/isaac-sim-quickstart">
              Run the QuickStart
            </Link>
            <Link className="button button-secondary" href="/docs/overview">
              Learn the architecture
            </Link>
          </div>
        </div>
        <div className="hero-console" aria-label="JenAI execution example">
          <div className="console-head"><span /><span /><span /><b>JenAI · approved mode</b></div>
          <div className="console-body">
            <p><i>›</i> Inspect every configured area, preserve evidence, then return home.</p>
            <p><span>Understanding</span> Resolve the mission goal and active site</p>
            <p><span>Selecting</span> Semantic area-patrol workflow</p>
            <p><span>Executing</span> Coverage · Nav2 · inspection · bounded retries</p>
            <p><span>Verifying</span> Required coverage · evidence · return home</p>
            <p className="console-success">● succeeded · completion contract passed</p>
          </div>
        </div>
      </section>

      <section className="home-section">
        <div className="section-heading">
          <span>Task-based documentation</span>
          <h2>Go from first run to evidence-backed deployment.</h2>
        </div>
        <div className="card-grid">
          {cards.map((card) => (
            <Link href={card.href} className="feature-card" key={card.title}>
              <small>{card.kicker}</small>
              <h3>{card.title}</h3>
              <p>{card.text}</p>
              <span>Open guide →</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="principles">
        <div>
          <strong>Natural language first</strong>
          <span>Slash commands remain shortcuts, not the product’s brain.</span>
        </div>
        <div>
          <strong>Workflow, not a token loop</strong>
          <span>Normal task steps run deterministically; only unresolved events return to the LLM.</span>
        </div>
        <div>
          <strong>No invented success</strong>
          <span>Missing evidence is unverified—not silently accepted.</span>
        </div>
      </section>
    </DocsShell>
  );
}
