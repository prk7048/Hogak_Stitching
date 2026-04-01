export type AppRouteGroup = "operate" | "calibration" | "diagnostics";

export type AppRouteMeta = {
  path: string;
  label: string;
  shortLabel?: string;
  group: AppRouteGroup;
  summary: string;
};

export type AppRouteGroupMeta = {
  id: AppRouteGroup;
  label: string;
  path: string;
  summary: string;
};

export const APP_ROUTE_GROUPS: AppRouteGroupMeta[] = [
  {
    id: "operate",
    label: "운영",
    path: "/dashboard",
    summary: "준비된 winner geometry로 송출을 시작하고 현재 출력 상태를 확인합니다.",
  },
  {
    id: "calibration",
    label: "Bakeoff",
    path: "/geometry-compare",
    summary: "4개 후보를 auto-only bakeoff로 비교하고 winner를 freeze/promote 합니다.",
  },
  {
    id: "diagnostics",
    label: "진단",
    path: "/validation",
    summary: "런타임 검증과 artifact 상태를 확인합니다.",
  },
];

export const APP_ROUTES: AppRouteMeta[] = [
  {
    path: "/dashboard",
    label: "대시보드",
    shortLabel: "운영",
    group: "operate",
    summary: "Start로 정렬 미리보기와 실제 stitched 송출을 순서대로 확인합니다.",
  },
  {
    path: "/outputs",
    label: "출력",
    group: "operate",
    summary: "외부 플레이어 수신 주소와 출력 런타임 상태를 확인합니다.",
  },
  {
    path: "/geometry-compare",
    label: "Geometry Bakeoff",
    shortLabel: "Bakeoff",
    group: "calibration",
    summary: "left-anchor와 virtual-center 계열 4개 후보를 비교하고 winner를 선택합니다.",
  },
  {
    path: "/validation",
    label: "검증",
    group: "diagnostics",
    summary: "현재 geometry artifact, checksum, launch readiness를 확인합니다.",
  },
  {
    path: "/artifacts",
    label: "Artifacts",
    group: "diagnostics",
    summary: "운영에 노출되는 geometry artifact와 메타데이터를 확인합니다.",
  },
];

export function routeForPath(pathname: string): AppRouteMeta | null {
  return APP_ROUTES.find((route) => route.path === pathname) ?? null;
}

export function routesForGroup(group: AppRouteGroup): AppRouteMeta[] {
  return APP_ROUTES.filter((route) => route.group === group);
}

export function routeGroupForPath(pathname: string): AppRouteGroupMeta {
  const route = routeForPath(pathname);
  return APP_ROUTE_GROUPS.find((group) => group.id === route?.group) ?? APP_ROUTE_GROUPS[0];
}
