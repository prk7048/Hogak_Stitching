export type ProjectState = {
  status?: string;
  start_phase?: string;
  status_message?: string;
  running?: boolean;
  can_start?: boolean;
  can_stop?: boolean;
  blocker_reason?: string;
  output_receive_uri?: string;
  production_output_target?: string;
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
};

export type ProjectActionResponse = {
  ok?: boolean;
  message?: string;
  detail?: string;
  error?: string;
  state?: ProjectState;
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

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => asString(item)).filter((item) => item.length > 0);
}

function normalizeProjectState(value: unknown): ProjectState {
  if (!isRecord(value)) {
    return {};
  }
  return {
    status: asString(value.status, "unknown"),
    start_phase: asString(value.start_phase),
    status_message: asString(value.status_message),
    running: asBoolean(value.running, false),
    can_start: asBoolean(value.can_start, false),
    can_stop: asBoolean(value.can_stop, false),
    blocker_reason: asString(value.blocker_reason),
    output_receive_uri: asString(value.output_receive_uri),
    production_output_target: asString(value.production_output_target),
    runtime_active_model: asString(value.runtime_active_model),
    runtime_active_residual_model: asString(value.runtime_active_residual_model || value.geometry_residual_model),
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

export async function fetchProjectState(): Promise<ProjectState> {
  try {
    const payload = await requestJson<unknown>("/api/project/state");
    return normalizeProjectState(payload);
  } catch {
    return {
      status: "error",
      start_phase: "error",
      status_message: "Backend unavailable. Live runtime truth is not available.",
      running: false,
      can_start: false,
      can_stop: false,
      blocker_reason: "Backend unavailable",
      output_receive_uri: "",
      production_output_target: "",
      runtime_active_model: "",
      runtime_active_residual_model: "",
      runtime_active_artifact_path: "",
      runtime_artifact_checksum: "",
      runtime_launch_ready: false,
      runtime_launch_ready_reason: "Backend unavailable",
      fallback_used: false,
      gpu_path_mode: "unknown",
      gpu_path_ready: false,
      input_path_mode: "",
      output_path_mode: "",
      output_path_direct: false,
      output_path_bridge: false,
      zero_copy_ready: false,
      zero_copy_reason: "Backend unavailable",
      zero_copy_blockers: [],
    };
  }
}

export async function startProject(): Promise<ProjectActionResponse> {
  return postJson<ProjectActionResponse>("/api/project/start", {});
}

export async function stopProject(): Promise<ProjectActionResponse> {
  return postJson<ProjectActionResponse>("/api/project/stop", {});
}

export function describeProjectActionResult(result: unknown): string {
  if (!isRecord(result)) {
    return "Request completed.";
  }
  const message = asString(result.message || result.detail || result.error);
  if (message) {
    return message;
  }
  return result.ok === false ? "Request failed." : "Request completed.";
}
