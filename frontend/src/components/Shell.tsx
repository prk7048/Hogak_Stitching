import { NavLink } from "react-router-dom";
import type { ReactNode } from "react";

import { apiUrl } from "../lib/api";

type ShellProps = {
  children: ReactNode;
};

const navItems = [
  ["/dashboard", "Dashboard"],
  ["/validation", "Validation"],
  ["/geometry-compare", "Geometry Compare"],
  ["/outputs", "Outputs"],
  ["/artifacts", "Artifacts"],
] as const;

export function Shell({ children }: ShellProps) {
  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">Hogak Operator</div>
          <h1>FastAPI control, React operator surface</h1>
        </div>
        <div className="topbar-chip">REST + SSE + JPEG polling</div>
      </header>
      <nav className="nav">
        {navItems.map(([to, label]) => (
          <NavLink key={to} to={to} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            {label}
          </NavLink>
        ))}
        <a className="nav-link" href={apiUrl("/legacy/calibration/")}>
          Legacy Calibration
        </a>
      </nav>
      <main className="content">{children}</main>
    </div>
  );
}
