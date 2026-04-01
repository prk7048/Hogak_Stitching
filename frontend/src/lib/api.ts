export type RuntimeState = {
  running?: boolean;
  prepared?: boolean;
  status?: string;
  last_error?: string;
  runtime_active_model?: string;
  runtime_active_residual_model?: string;
  runtime_active_artifact_path?: string;
  runtime_artifact_checksum?: string;
  runtime_launch_ready?: boolean;
  runtime_launch_ready_reason?: string;
  fallback_used?: boolean;
  gpu_path_mode?: string;
  gpu_path_ready?: boolean;
  input_path_mode?: string;
  output_path_mode?: string;
  output_path_direct?: boolean;
  output_path_bridge?: boolean;
  zero_copy_ready?: boolean;
  zero_copy_reason?: string;
  zero_copy_blockers?: string[];
  validation_mode?: string;
  strict_fresh?: boolean;
  preview_ready?: boolean;
  preview_left_url?: string;
  preview_right_url?: string;
  preview_stitched_url?: string;
  production_output_runtime_mode?: string;
  production_output_target?: string;
  production_output_frames_dropped?: number;
  production_output_frames_written?: number;
  production_output_written_fps?: number;
  stitch_actual_fps?: number;
  worker_fps?: number;
  reused_count?: number;
  wait_paired_fresh_count?: number;
  [key: string]: unknown;
};

export type RuntimeEvent = {
  seq?: number;
  type: string;
  timestamp_sec?: number;
  payload?: Record<string, unknown>;
  raw?: unknown;
};

export type RuntimeActionResponse = {
  ok?: boolean;
  message?: string;
  detail?: string;
  error?: string;
  state?: RuntimeState;
  [key: string]: unknown;
};

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

function asBoolean(value: unknown, fallback = false): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    return value !== 0;
  }
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["1", "true", "yes", "on"].includes(normalized)) {
      return true;
    }
    if (["0", "false", "no", "off"].includes(normalized)) {
      return false;
    }
  }
  return fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => asString(item)).filter((item) => item.length > 0);
}

function normalizeRuntimeStateShape(value: unknown): RuntimeState {
  if (!isRecord(value)) {
    return {};
  }
  return {
    running: asBoolean(value.running, false),
    prepared: asBoolean(value.prepared, false),
    status: asString(value.status),
    last_error: asString(value.last_error),
    runtime_active_model: asString(value.runtime_active_model),
    runtime_active_residual_model: asString(value.runtime_active_residual_model),
    runtime_active_artifact_path: asString(value.runtime_active_artifact_path),
    runtime_artifact_checksum: asString(value.runtime_artifact_checksum),
    runtime_launch_ready: asBoolean(value.runtime_launch_ready, false),
    runtime_launch_ready_reason: asString(value.runtime_launch_ready_reason),
    fallback_used: asBoolean(value.fallback_used, false),
    gpu_path_mode: asString(value.gpu_path_mode, "unknown"),
    gpu_path_ready: asBoolean(value.gpu_path_ready, false),
    input_path_mode: asString(value.input_path_mode),
    output_path_mode: asString(value.output_path_mode),
    output_path_direct: asBoolean(value.output_path_direct, false),
    output_path_bridge: asBoolean(value.output_path_bridge, false),
    zero_copy_ready: asBoolean(value.zero_copy_ready, false),
    zero_copy_reason: asString(value.zero_copy_reason),
    zero_copy_blockers: asStringArray(value.zero_copy_blockers),
    validation_mode: asString(value.validation_mode),
    strict_fresh: asBoolean(value.strict_fresh, false),
    preview_ready: asBoolean(value.preview_ready, false),
    preview_left_url: asString(value.preview_left_url),
    preview_right_url: asString(value.preview_right_url),
    preview_stitched_url: asString(value.preview_stitched_url),
    production_output_runtime_mode: asString(value.production_output_runtime_mode),
    production_output_target: asString(value.production_output_target),
    production_output_frames_dropped: asNumber(value.production_output_frames_dropped, 0),
    production_output_frames_written: asNumber(value.production_output_frames_written, 0),
    production_output_written_fps: asNumber(value.production_output_written_fps, 0),
    stitch_actual_fps: asNumber(value.stitch_actual_fps, 0),
    worker_fps: asNumber(value.worker_fps, 0),
    reused_count: asNumber(value.reused_count, 0),
    wait_paired_fresh_count: asNumber(value.wait_paired_fresh_count, 0),
  };
}

function parseRuntimeEvent(data: string, fallbackType: string): RuntimeEvent {
  try {
    const parsed = JSON.parse(data);
    if (!isRecord(parsed)) {
      return { type: fallbackType, raw: parsed };
    }
    return {
      seq: asNumber(parsed.seq, 0) || undefined,
      type: asString(parsed.type, fallbackType),
      timestamp_sec: asNumber(parsed.timestamp_sec, 0) || undefined,
      payload: isRecord(parsed.payload) ? parsed.payload : undefined,
      raw: parsed,
    };
  } catch {
    return { type: fallbackType, raw: data };
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(joinPath(path), { ...init, headers });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail =
      (isRecord(payload) && asString(payload.detail || payload.error || payload.message)) || `${response.status}`;
    throw new Error(detail);
  }
  return payload as T;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    body: body === undefined ? "{}" : JSON.stringify(body),
  });
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
  } catch {
    return {
      status: "backend unavailable",
      runtime_active_model: "",
      runtime_launch_ready: false,
      runtime_launch_ready_reason: "Backend unavailable",
      gpu_path_mode: "unknown",
      gpu_path_ready: false,
    };
  }
}

export async function previewAlignRuntime(body?: unknown): Promise<RuntimeActionResponse> {
  return postJson<RuntimeActionResponse>("/api/runtime/preview-align", body ?? {});
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

export function describeRuntimeActionResult(result: unknown): string {
  if (!isRecord(result)) {
    return "Request completed.";
  }
  const message = asString(result.message || result.detail || result.error);
  if (message) {
    return message;
  }
  if (result.ok === false) {
    return "Request failed.";
  }
  if (result.ok === true) {
    const state = normalizeRuntimeState(result.state);
    if (state.status) {
      return `OK: ${state.status}`;
    }
    return "OK";
  }
  return "Request completed.";
}

function runtimeEventsUrl(): string {
  return joinPath("/_internal/runtime/events");
}

export function outputReceiveUri(target: unknown): string {
  const text = asString(target);
  if (!text) {
    return "";
  }
  if (!text.startsWith("udp://")) {
    return text;
  }
  const endpoint = text.split("?", 1)[0].slice("udp://".length);
  const hostPort = endpoint.startsWith("@") ? endpoint.slice(1) : endpoint;
  const separator = hostPort.lastIndexOf(":");
  if (separator < 0) {
    return text;
  }
  const port = hostPort.slice(separator + 1).trim();
  return port ? `udp://@:${port}?fifo_size=${8 * 1024 * 1024}&overrun_nonfatal=1` : text;
}

export function openRuntimeEventStream(onEvent: (event: RuntimeEvent) => void): EventSource | null {
  if (typeof window.EventSource === "undefined") {
    return null;
  }
  const handleEvent = (event: Event) => {
    if (event instanceof MessageEvent) {
      onEvent(parseRuntimeEvent(String(event.data), event.type || "message"));
      return;
    }
    onEvent({ type: event.type || "message", raw: event });
  };

  try {
    const source = new EventSource(runtimeEventsUrl());
    const namedEvents = ["metrics", "status", "hello", "stopped", "error"] as const;
    for (const eventType of namedEvents) {
      source.addEventListener(eventType, handleEvent as EventListener);
    }
    source.addEventListener("message", handleEvent as EventListener);
    return source;
  } catch {
    return null;
  }
}
