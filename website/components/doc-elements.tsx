import type { ReactNode } from "react";

export function CodeBlock({
  children,
  label,
}: {
  children: string;
  label?: string;
}) {
  return (
    <div className="code-block">
      {label ? <div className="code-label">{label}</div> : null}
      <pre>
        <code>{children.trim()}</code>
      </pre>
    </div>
  );
}

export function Callout({
  title,
  tone = "info",
  children,
}: {
  title: string;
  tone?: "info" | "success" | "warning";
  children: ReactNode;
}) {
  return (
    <aside className={`callout callout-${tone}`}>
      <strong>{title}</strong>
      <div>{children}</div>
    </aside>
  );
}

export function Steps({ children }: { children: ReactNode }) {
  return <ol className="steps">{children}</ol>;
}

export function StatusPill({
  children,
  tone,
}: {
  children: ReactNode;
  tone: "good" | "warn" | "bad" | "neutral";
}) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}
