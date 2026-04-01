import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { PRIMARY_ROUTES, primaryRouteForPath } from "../lib/routes";

type ShellProps = {
  children: ReactNode;
};

export function Shell({ children }: ShellProps) {
  const location = useLocation();
  const activePrimaryRoute = primaryRouteForPath(location.pathname);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-copy">
          <div className="eyebrow">Hogak Operator Surface</div>
          <h1>Product surface: Run and Validate only</h1>
          <p className="topbar-summary">
            Geometry comparison, bakeoff, and legacy calibration are no longer part of the public product flow. This
            surface focuses only on <strong>active mesh runtime truth</strong> and <strong>actual transmit state</strong>.
          </p>
        </div>
        <div className="topbar-chip">
          <span className="topbar-chip-label">Current page</span>
          <strong>{activePrimaryRoute.label}</strong>
        </div>
      </header>

      <nav className="nav nav-primary" aria-label="Primary workflow">
        {PRIMARY_ROUTES.map((route) => (
          <NavLink
            key={route.path}
            to={route.path}
            className={() => `nav-link${route.path === activePrimaryRoute.path ? " active" : ""}`}
          >
            <span className="nav-link-title">{route.label}</span>
            <span className="nav-link-summary">{route.summary}</span>
          </NavLink>
        ))}
      </nav>

      <main className="content">{children}</main>
    </div>
  );
}
