export type AppRouteSection = "bakeoff" | "run" | "validate";
export type AppRouteTier = "primary" | "secondary";

export type AppRouteMeta = {
  path: string;
  label: string;
  summary: string;
  section: AppRouteSection;
  tier: AppRouteTier;
};

export const PRIMARY_ROUTES: AppRouteMeta[] = [
  {
    path: "/bakeoff",
    label: "Bakeoff",
    summary: "4개 후보를 비교하고 winner를 선택합니다.",
    section: "bakeoff",
    tier: "primary",
  },
  {
    path: "/run",
    label: "Run",
    summary: "승격된 geometry로 정렬 미리보기와 송출을 진행합니다.",
    section: "run",
    tier: "primary",
  },
  {
    path: "/validate",
    label: "Validate",
    summary: "선택 모델, 승격 모델, 실제 active 모델을 검증합니다.",
    section: "validate",
    tier: "primary",
  },
];

export const SECONDARY_ROUTES: AppRouteMeta[] = [
  {
    path: "/outputs",
    label: "출력",
    summary: "외부 플레이어 수신 주소와 출력 상태를 확인합니다.",
    section: "run",
    tier: "secondary",
  },
  {
    path: "/artifacts",
    label: "Artifacts",
    summary: "생성된 geometry artifact와 메타데이터를 봅니다.",
    section: "validate",
    tier: "secondary",
  },
];

export const APP_ROUTES: AppRouteMeta[] = [...PRIMARY_ROUTES, ...SECONDARY_ROUTES];

export function routeForPath(pathname: string): AppRouteMeta | null {
  return APP_ROUTES.find((route) => route.path === pathname) ?? null;
}

export function primaryRouteForPath(pathname: string): AppRouteMeta {
  const route = routeForPath(pathname);
  if (route?.tier === "primary") {
    return route;
  }
  const owner = PRIMARY_ROUTES.find((candidate) => candidate.section === route?.section);
  return owner ?? PRIMARY_ROUTES[0];
}

export function secondaryRoutesForSection(section: AppRouteSection): AppRouteMeta[] {
  return SECONDARY_ROUTES.filter((route) => route.section === section);
}
