export type AppRouteSection = "run" | "validate";
export type AppRouteTier = "primary";

export type AppRouteMeta = {
  path: string;
  label: string;
  summary: string;
  section: AppRouteSection;
  tier: AppRouteTier;
};

export const PRIMARY_ROUTES: AppRouteMeta[] = [
  {
    path: "/run",
    label: "Run",
    summary: "Preview alignment, then start or stop transmit.",
    section: "run",
    tier: "primary",
  },
  {
    path: "/validate",
    label: "Validate",
    summary: "Check active mesh runtime truth and launch readiness.",
    section: "validate",
    tier: "primary",
  },
];

export function routeForPath(pathname: string): AppRouteMeta | null {
  return PRIMARY_ROUTES.find((route) => route.path === pathname) ?? null;
}

export function primaryRouteForPath(pathname: string): AppRouteMeta {
  return routeForPath(pathname) ?? PRIMARY_ROUTES[0];
}

export function secondaryRoutesForSection(_section: AppRouteSection): AppRouteMeta[] {
  return [];
}
