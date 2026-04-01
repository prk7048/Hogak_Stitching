import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { PRIMARY_ROUTES, primaryRouteForPath, routeForPath, secondaryRoutesForSection } from "../lib/routes";

type ShellProps = {
  children: ReactNode;
};

export function Shell({ children }: ShellProps) {
  const location = useLocation();
  const activePrimaryRoute = primaryRouteForPath(location.pathname);
  const activeRoute = routeForPath(location.pathname) ?? activePrimaryRoute;
  const secondaryRoutes = secondaryRoutesForSection(activePrimaryRoute.section);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-copy">
          <div className="eyebrow">Hogak Operator Surface</div>
          <h1>Bakeoff winner를 고른 뒤, 승격된 geometry로만 송출합니다.</h1>
          <p className="topbar-summary">
            현재 운영 흐름은 <strong>Bakeoff -&gt; Run -&gt; Validate</strong>로 고정되어 있습니다. 먼저 4개 후보를
            비교하고, 선택한 winner가 실제 runtime에 올라갈 수 있는지 확인한 뒤, 정렬 미리보기와 송출을 진행합니다.
          </p>
        </div>
        <div className="topbar-chip">
          <span className="topbar-chip-label">현재 위치</span>
          <strong>{activePrimaryRoute.label}</strong>
          <span>{activeRoute.label}</span>
        </div>
      </header>

      <nav className="nav nav-primary" aria-label="주요 워크플로">
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

      {secondaryRoutes.length > 0 ? (
        <nav className="nav nav-secondary" aria-label={`${activePrimaryRoute.label} 보조 페이지`}>
          {secondaryRoutes.map((route) => (
            <NavLink
              key={route.path}
              to={route.path}
              className={({ isActive }) => `nav-link nav-link-secondary${isActive ? " active" : ""}`}
            >
              <span className="nav-link-title">{route.label}</span>
              <span className="nav-link-summary">{route.summary}</span>
            </NavLink>
          ))}
        </nav>
      ) : null}

      <main className="content">{children}</main>
    </div>
  );
}
