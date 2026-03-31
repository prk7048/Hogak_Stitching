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
    summary: "런타임을 준비하고 시작한 뒤 stitched 출력 상태를 확인합니다.",
  },
  {
    id: "calibration",
    label: "캘리브레이션",
    path: "/calibration/start",
    summary: "실행 전에 기하 정보를 만들거나 승인합니다.",
  },
  {
    id: "diagnostics",
    label: "점검",
    path: "/validation",
    summary: "검증을 수행하고 기하 및 아티팩트를 점검합니다.",
  },
];

export const APP_ROUTES: AppRouteMeta[] = [
  {
    path: "/dashboard",
    label: "대시보드",
    shortLabel: "홈",
    group: "operate",
    summary: "주요 런타임 제어, 미리보기, 출력 상태를 확인합니다.",
  },
  {
    path: "/outputs",
    label: "출력",
    group: "operate",
    summary: "수신 URI, writer 모드, 플레이어 안내를 확인합니다.",
  },
  {
    path: "/calibration/start",
    label: "시작",
    group: "calibration",
    summary: "기존 기하를 재사용할지, 새 점 선택을 시작할지 고릅니다.",
  },
  {
    path: "/calibration/assisted",
    label: "점 선택",
    group: "calibration",
    summary: "좌우 대응점을 선택해 후보 기하를 계산합니다.",
  },
  {
    path: "/calibration/review",
    label: "검토",
    group: "calibration",
    summary: "승인 전에 스티치 품질과 inlier 상태를 확인합니다.",
  },
  {
    path: "/calibration/stitch-review",
    label: "승인",
    group: "calibration",
    summary: "저장된 기하를 확인하고 런타임 시작 단계로 이동합니다.",
  },
  {
    path: "/validation",
    label: "검증",
    group: "diagnostics",
    summary: "런타임 상태를 바꾸기 전 읽기 전용 검증을 수행합니다.",
  },
  {
    path: "/geometry-compare",
    label: "기하 비교",
    group: "diagnostics",
    summary: "기본 경로와 후보 아티팩트를 비교합니다.",
  },
  {
    path: "/artifacts",
    label: "아티팩트",
    group: "diagnostics",
    summary: "저장된 런타임 기하와 메타데이터를 확인합니다.",
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
