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
          <h1>자연스러운 winner를 고르고, 준비된 geometry만 송출합니다.</h1>
          <p className="topbar-summary">
            이 브랜치는 수동 점 선택 대신 auto-only bakeoff를 기준으로 움직입니다. 먼저 Bakeoff에서 후보를 비교하고
            winner를 freeze/promote 한 뒤, 운영 화면에서 Start로 정렬 미리보기와 실제 stitched 송출을 확인합니다.
          </p>
        </div>
        <div className="topbar-chip">
          <span className="topbar-chip-label">현재 위치</span>
          <strong>{activeGroup.label}</strong>
          <span>{activeRoute?.label ?? "대시보드"}</span>
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
