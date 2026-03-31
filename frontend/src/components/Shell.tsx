import { NavLink, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { APP_ROUTE_GROUPS, routeForPath, routeGroupForPath, routesForGroup } from "../lib/routes";

type ShellProps = {
  children: ReactNode;
};

export function Shell({ children }: ShellProps) {
  const location = useLocation();
  const activeGroup = routeGroupForPath(location.pathname);
  const activeRoute = routeForPath(location.pathname);
  const secondaryRoutes = routesForGroup(activeGroup.id);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-copy">
          <div className="eyebrow">Hogak 운영 화면</div>
          <h1>한 화면에서 보정하고, 실행하고, 점검하세요</h1>
          <p className="topbar-summary">
            기본 동선은 단순하게 유지합니다. 캘리브레이션에서 기하를 승인하고, 운영에서 런타임을 제어하고,
            깊은 점검은 점검 화면에서 확인합니다.
          </p>
        </div>
        <div className="topbar-chip">
          <span className="topbar-chip-label">현재 위치</span>
          <strong>{activeGroup.label}</strong>
          <span>{activeRoute?.label ?? "운영 화면"}</span>
        </div>
      </header>

      <nav className="nav nav-primary" aria-label="주요 내비게이션">
        {APP_ROUTE_GROUPS.map((group) => (
          <NavLink
            key={group.id}
            to={group.path}
            className={() => `nav-link${group.id === activeGroup.id ? " active" : ""}`}
          >
            <span className="nav-link-title">{group.label}</span>
            <span className="nav-link-summary">{group.summary}</span>
          </NavLink>
        ))}
      </nav>

      <nav className="nav nav-secondary" aria-label={`${activeGroup.label} 세부 페이지`}>
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

      <main className="content">{children}</main>
    </div>
  );
}
