import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  eyebrow?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function Panel({ title, eyebrow, action, className = "", children }: PanelProps) {
  return (
    <section className={`panel ${className}`}>
      <header className="panel__header">
        <div>
          {eyebrow ? <span className="panel__eyebrow">{eyebrow}</span> : null}
          <h2>{title}</h2>
        </div>
        {action ? <div className="panel__action">{action}</div> : null}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}

interface BadgeProps {
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
  children: ReactNode;
}

export function Badge({ tone = "neutral", children }: BadgeProps) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}
