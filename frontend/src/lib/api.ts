export type RuntimeState = Record<string, unknown>;

export type RuntimeEvent = {
  seq?: number;
  type: string;
  timestamp_sec?: number;
  payload?: Record<string, unknown>;
  raw?: unknown;
};

export type GeometryArtifactSummary = {
  name: string;
  path?: string;
  artifact_type?: string;
  schema_version?: number;
  model?: string;
  geometry_model?: string;
  output_resolution?: number[];
  calibration?: Record<string, unknown>;
  source?: Record<string, unknown>;
  raw?: unknown;
};

export type RuntimeActionResponse = Record<string, unknown>;

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || "";

function joinPath(path: string): string {
  if (!apiBaseUrl) {
    return path;
  }
  return `${apiBaseUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return fallback;
}

function pickString(...values: unknown[]): string {
  for (const value of values) {
    const text = asString(value, "");
    if (text) {
      return text;
    }
  }
  return "";
}

function normalizeRuntimeStateShape(value: unknown): RuntimeState {
  if (!isRecord(value)) {
    return {};
  }

  const flattened: RuntimeState = {};
  const candidates = [
    value.state,
    value.latest_metrics,
    value.latest_hello,
    value.prepared_plan,
    value.prepared,
    value.metrics,
    value.hello,
    value.payload,
  ];
  for (const candidate of candidates) {
    if (isRecord(candidate)) {
      Object.assign(flattened, candidate);
    }
  }

  Object.assign(flattened, value);

  if (!("status" in flattened)) {
    const running = Boolean(flattened.running);
    const prepared = Boolean(flattened.prepared);
    flattened.status = running ? "running" : prepared ? "prepared" : "idle";
  }

  if (!("geometry_mode" in flattened)) {
    const preparedPlan = isRecord(flattened.prepared_plan) ? flattened.prepared_plan : null;
    const preparedGeometry = preparedPlan && isRecord(preparedPlan.geometry) ? preparedPlan.geometry : null;
    flattened.geometry_mode = pickString(
      flattened.geometry_mode,
      isRecord(flattened.geometry) ? flattened.geometry.model : "",
      flattened.latest_geometry_mode,
      preparedPlan?.geometry_model,
      preparedGeometry?.model,
      "planar-homography",
    );
  }

  if (!("seam_mode" in flattened)) {
    const seam = isRecord(flattened.seam) ? flattened.seam : null;
    flattened.seam_mode = pickString(flattened.latest_seam_mode, seam?.mode, "feather");
  }

  if (!("exposure_mode" in flattened)) {
    const exposure = isRecord(flattened.exposure) ? flattened.exposure : null;
    flattened.exposure_mode = pickString(flattened.latest_exposure_mode, exposure?.mode, "none");
  }

  if (!("output_runtime_mode" in flattened)) {
    const preparedPlan = isRecord(flattened.prepared_plan) ? flattened.prepared_plan : null;
    flattened.output_runtime_mode = pickString(
      flattened.latest_output_runtime_mode,
      flattened.prepared_output_runtime_mode,
      preparedPlan?.output_runtime_mode,
      "unknown",
    );
  }

  if (!("production_output_runtime_mode" in flattened)) {
    const preparedPlan = isRecord(flattened.prepared_plan) ? flattened.prepared_plan : null;
    flattened.production_output_runtime_mode = pickString(
      flattened.latest_production_output_runtime_mode,
      flattened.prepared_production_output_runtime_mode,
      preparedPlan?.production_output_runtime_mode,
      "unknown",
    );
  }

  if (!("output_target" in flattened)) {
    const preparedPlan = isRecord(flattened.prepared_plan) ? flattened.prepared_plan : null;
    flattened.output_target = pickString(preparedPlan?.probe_target, preparedPlan?.output_target, "n/a");
  }

  if (!("production_output_target" in flattened)) {
    const preparedPlan = isRecord(flattened.prepared_plan) ? flattened.prepared_plan : null;
    flattened.production_output_target = pickString(
      preparedPlan?.transmit_target,
      preparedPlan?.production_output_target,
      "n/a",
    );
  }

  return flattened;
}

function normalizeGeometryArtifact(item: unknown, index: number): GeometryArtifactSummary | null {
  if (!isRecord(item)) {
    return null;
  }

  const name = pickString(item.name, item.path, item.artifact_name, item.file_name, `artifact-${index + 1}`);
  const geometry = isRecord(item.geometry) ? item.geometry : null;
  const calibration = isRecord(item.calibration) ? item.calibration : undefined;
  const source = isRecord(item.source) ? item.source : undefined;

  return {
    name,
    path: asString(item.path, ""),
    artifact_type: asString(item.artifact_type, ""),
    schema_version: typeof item.schema_version === "number" ? item.schema_version : undefined,
    model: pickString(item.model, geometry?.model),
    geometry_model: pickString(item.geometry_model, geometry?.model),
    output_resolution: Array.isArray(item.output_resolution) ? item.output_resolution.map((value) => Number(value)) : undefined,
    calibration,
    source,
    raw: item,
  };
}

function parseRuntimeEvent(raw: string, fallbackType = "message"): RuntimeEvent {
  const parsed = JSON.parse(raw) as unknown;
  if (!isRecord(parsed)) {
    return {
      type: fallbackType,
      raw: parsed,
    };
  }
  const payload = isRecord(parsed.payload) ? parsed.payload : undefined;
  return {
    seq: typeof parsed.seq === "number" ? parsed.seq : undefined,
    type: asString(parsed.type, fallbackType) || fallbackType,
    timestamp_sec: typeof parsed.timestamp_sec === "number" ? parsed.timestamp_sec : undefined,
    payload,
    raw: parsed,
  };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(joinPath(path), {
    ...init,
    headers,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail ? `${response.status}: ${detail}` : `request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method: "POST",
    body: body === undefined ? "{}" : JSON.stringify(body),
  };
  return requestJson<T>(path, init);
}

export function apiUrl(path: string): string {
  return joinPath(path);
}

export function normalizeRuntimeState(value: unknown): RuntimeState {
  return normalizeRuntimeStateShape(value);
}

export async function fetchRuntimeState(): Promise<RuntimeState> {
  try {
    const payload = await requestJson<unknown>("/api/runtime/state");
    return normalizeRuntimeState(payload);
  } catch (error) {
    return {
      status: "backend unavailable",
      geometry_mode: "planar-homography",
      seam_mode: "feather",
      exposure_mode: "none",
      output_runtime_mode: "unknown",
      production_output_runtime_mode: "unknown",
      error: String(error),
    };
  }
}

export async function fetchGeometryArtifacts(): Promise<GeometryArtifactSummary[]> {
  try {
    const payload = await requestJson<unknown>("/api/artifacts/geometry");
    const items = isRecord(payload) && Array.isArray(payload.items) ? payload.items : Array.isArray(payload) ? payload : [];
    return items
      .map((item, index) => normalizeGeometryArtifact(item, index))
      .filter((item): item is GeometryArtifactSummary => item !== null);
  } catch {
    return [];
  }
}

export async function fetchGeometryArtifact(name: string): Promise<GeometryArtifactSummary | null> {
  try {
    const payload = await requestJson<unknown>(`/api/artifacts/geometry/${encodeURIComponent(name)}`);
    return normalizeGeometryArtifact(payload, 0);
  } catch {
    return null;
  }
}

export async function prepareRuntime(body?: unknown): Promise<RuntimeActionResponse> {
  return postJson<RuntimeActionResponse>("/api/runtime/prepare", body ?? {});
}

export async function startRuntime(body?: unknown): Promise<RuntimeActionResponse> {
  return postJson<RuntimeActionResponse>("/api/runtime/start", body ?? {});
}

export async function stopRuntime(): Promise<RuntimeActionResponse> {
  return postJson<RuntimeActionResponse>("/api/runtime/stop", {});
}

export async function validateRuntime(body?: unknown): Promise<RuntimeActionResponse> {
  return postJson<RuntimeActionResponse>("/api/runtime/validate", body ?? {});
}

export async function reloadRuntime(body: unknown): Promise<RuntimeActionResponse> {
  return postJson<RuntimeActionResponse>("/api/runtime/reload", body);
}

export function describeRuntimeActionResult(result: unknown): string {
  if (!isRecord(result)) {
    return "action completed";
  }
  const message = pickString(result.message, result.detail, result.error);
  if (message) {
    return message;
  }
  if (result.ok === false) {
    return "request failed";
  }
  if (result.ok === true) {
    const state = isRecord(result.state) ? result.state : null;
    if (state) {
      return `ok: ${pickString(state.status, state.state, "state updated")}`;
    }
    const plan = isRecord(result.plan) ? result.plan : null;
    if (plan) {
      return `ok: ${pickString(plan.geometry_artifact_path, plan.output_runtime_mode, "plan updated")}`;
    }
    return "ok";
  }
  return "action completed";
}

export function runtimeEventsUrl(): string {
  return joinPath("/api/runtime/events");
}

export function previewUrl(version: number): string {
  return joinPath(`/api/runtime/preview.jpg?ts=${version}`);
}

export function openRuntimeEventStream(onEvent: (event: RuntimeEvent) => void): EventSource | null {
  if (typeof window.EventSource === "undefined") {
    return null;
  }

  const handleEvent = (event: Event) => {
    if (event instanceof MessageEvent) {
      try {
        onEvent(parseRuntimeEvent(String(event.data), event.type || "message"));
      } catch {
        onEvent({ type: event.type || "message", raw: event.data });
      }
      return;
    }
    onEvent({
      type: event.type || "message",
      raw: { transport: true, readyState: EventSource.CLOSED },
    });
  };

  try {
    const source = new EventSource(runtimeEventsUrl());
    const namedEvents = ["metrics", "status", "hello", "stopped", "error"] as const;
    for (const eventType of namedEvents) {
      source.addEventListener(eventType, handleEvent as EventListener);
    }
    source.addEventListener("message", handleEvent as EventListener);
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        onEvent({ type: "error", raw: { transport: "closed" } });
      }
    };
    return source;
  } catch {
    return null;
  }
}
