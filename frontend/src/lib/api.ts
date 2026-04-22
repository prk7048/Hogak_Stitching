export type ProjectLogEntry = {
  id?: number;
  timestamp_sec?: number;
  phase?: string;
  level?: string;
  message?: string;
};

export type ProjectDebugStep = {
  id?: string;
  label?: string;
  state?: string;
  message?: string;
  timestamp_sec?: number;
};

export type GeometryTruth = {
  model?: string;
  requested_residual_model?: string;
  residual_model?: string;
  artifact_path?: string;
  artifact_checksum?: string;
  launch_ready?: boolean;
  launch_ready_reason?: string;
  rollout_status?: string;
  fallback_used?: boolean;
  operator_visible?: boolean;
};

export type RuntimeTruth = {
  status?: string;
  running?: boolean;
  pid?: number;
  phase?: string;
  active_model?: string;
  active_residual_model?: string;
  gpu_path_mode?: string;
  gpu_path_ready?: boolean;
  input_path_mode?: string;
  output_path_mode?: string;
};

export type OutputTruth = {
  receive_uri?: string;
  target?: string;
  mode?: string;
  direct?: boolean;
  bridge?: boolean;
  bridge_reason?: string;
  last_error?: string;
};

export type ZeroCopyTruth = {
  ready?: boolean;
  reason?: string;
  blockers?: string[];
  status?: string;
};

export type ProjectDebug = {
  enabled?: boolean;
  current_stage?: string;
  steps?: ProjectDebugStep[];
};

export type ProjectState = {
  lifecycle_state?: string;
  phase?: string;
  status_message?: string;
  running?: boolean;
  can_start?: boolean;
  can_stop?: boolean;
  blocker_reason?: string;
  geometry?: GeometryTruth;
  runtime?: RuntimeTruth;
  output?: OutputTruth;
  zero_copy?: ZeroCopyTruth;
  recent_events?: ProjectLogEntry[];
  debug?: ProjectDebug;
};

export type ProjectActionResponse = {
  ok?: boolean;
  message?: string;
  detail?: string;
  error?: string;
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

function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function asProjectLogEntry(value: unknown): ProjectLogEntry | null {
  if (!isRecord(value)) {
    return null;
  }
  const message = asString(value.message);
  if (!message) {
    return null;
  }
  return {
    id: asNumber(value.id, 0),
    timestamp_sec: asNumber(value.timestamp_sec, 0),
    phase: asString(value.phase),
    level: asString(value.level, "info"),
    message,
  };
}

function asProjectLog(value: unknown): ProjectLogEntry[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => asProjectLogEntry(item)).filter((item): item is ProjectLogEntry => item !== null);
}

function asProjectDebugStep(value: unknown): ProjectDebugStep | null {
  if (!isRecord(value)) {
    return null;
  }
  const id = asString(value.id);
  const label = asString(value.label);
  if (!id && !label) {
    return null;
  }
  return {
    id,
    label,
    state: asString(value.state, "pending"),
    message: asString(value.message),
    timestamp_sec: asNumber(value.timestamp_sec, 0),
  };
}

function asProjectDebugSteps(value: unknown): ProjectDebugStep[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => asProjectDebugStep(item)).filter((item): item is ProjectDebugStep => item !== null);
}

function normalizeProjectState(value: unknown): ProjectState {
  if (!isRecord(value)) {
    return {};
  }
  const geometry = isRecord(value.geometry) ? value.geometry : {};
  const runtime = isRecord(value.runtime) ? value.runtime : {};
  const output = isRecord(value.output) ? value.output : {};
  const zeroCopy = isRecord(value.zero_copy) ? value.zero_copy : {};
  const debug = isRecord(value.debug) ? value.debug : {};
  const recentEvents = Array.isArray(value.recent_events) ? value.recent_events : value.project_log;
  const debugStepsValue = Array.isArray(debug.steps) ? debug.steps : value.debug_steps;
  const lifecycleState = asString(value.lifecycle_state || value.status, "unknown");
  const phase = asString(value.phase || value.start_phase);
  const activeModel = asString(geometry.model || runtime.active_model || value.runtime_active_model);
  const activeResidualModel = asString(
    geometry.residual_model || runtime.active_residual_model || value.runtime_active_residual_model || value.geometry_residual_model,
  );
  const artifactPath = asString(geometry.artifact_path || value.runtime_active_artifact_path);
  const artifactChecksum = asString(geometry.artifact_checksum || value.runtime_artifact_checksum);
  const launchReady = asBoolean(geometry.launch_ready ?? value.runtime_launch_ready, false);
  const launchReadyReason = asString(geometry.launch_ready_reason || value.runtime_launch_ready_reason);
  const outputMode = asString(output.mode || runtime.output_path_mode || value.output_path_mode);
  const outputDirect = asBoolean(output.direct ?? value.output_path_direct, false);
  const outputBridge = asBoolean(output.bridge ?? value.output_path_bridge, false);
  const zeroCopyReady = asBoolean(zeroCopy.ready ?? value.zero_copy_ready, false);
  const zeroCopyReason = asString(zeroCopy.reason || value.zero_copy_reason);
  const zeroCopyBlockers = asStringArray(zeroCopy.blockers || value.zero_copy_blockers);

  return {
    lifecycle_state: lifecycleState,
    phase,
    status_message: asString(value.status_message),
    running: asBoolean(value.running, false),
    can_start: asBoolean(value.can_start, false),
    can_stop: asBoolean(value.can_stop, false),
    blocker_reason: asString(value.blocker_reason),
    geometry: {
      model: activeModel,
      requested_residual_model: asString(geometry.requested_residual_model),
      residual_model: activeResidualModel,
      artifact_path: artifactPath,
      artifact_checksum: artifactChecksum,
      launch_ready: launchReady,
      launch_ready_reason: launchReadyReason,
      rollout_status: asString(geometry.rollout_status || value.geometry_rollout_status),
      fallback_used: asBoolean(geometry.fallback_used ?? value.fallback_used, false),
      operator_visible: asBoolean(geometry.operator_visible ?? value.geometry_operator_visible, false),
    },
    runtime: {
      status: asString(runtime.status || value.status, lifecycleState),
      running: asBoolean(runtime.running ?? value.running, false),
      pid: asNumber(runtime.pid, 0),
      phase,
      active_model: activeModel,
      active_residual_model: activeResidualModel,
      gpu_path_mode: asString(runtime.gpu_path_mode || value.gpu_path_mode, "unknown"),
      gpu_path_ready: asBoolean(runtime.gpu_path_ready ?? value.gpu_path_ready, false),
      input_path_mode: asString(runtime.input_path_mode || value.input_path_mode),
      output_path_mode: outputMode,
    },
    output: {
      receive_uri: asString(output.receive_uri || value.output_receive_uri),
      target: asString(output.target || value.production_output_target),
      mode: outputMode,
      direct: outputDirect,
      bridge: outputBridge,
      bridge_reason: asString(output.bridge_reason || value.output_bridge_reason),
      last_error: asString(output.last_error || value.production_output_last_error),
    },
    zero_copy: {
      ready: zeroCopyReady,
      reason: zeroCopyReason,
      blockers: zeroCopyBlockers,
      status: asString(zeroCopy.status, zeroCopyReady ? "ready" : zeroCopyBlockers.length > 0 ? "blocked" : "pending"),
    },
    recent_events: asProjectLog(recentEvents),
    debug: {
      enabled: asBoolean(debug.enabled ?? value.debug_mode, false),
      current_stage: asString(debug.current_stage || value.debug_current_stage),
      steps: asProjectDebugSteps(debugStepsValue),
    },
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
      lifecycle_state: "error",
      phase: "error",
      status_message: "Backend unavailable. Live runtime truth is not available.",
      running: false,
      can_start: false,
      can_stop: false,
      blocker_reason: "Backend unavailable",
      geometry: {
        model: "",
        requested_residual_model: "",
        residual_model: "",
        artifact_path: "",
        artifact_checksum: "",
        launch_ready: false,
        launch_ready_reason: "Backend unavailable",
        rollout_status: "",
        fallback_used: false,
        operator_visible: false,
      },
      runtime: {
        status: "error",
        running: false,
        pid: 0,
        phase: "error",
        active_model: "",
        active_residual_model: "",
        gpu_path_mode: "unknown",
        gpu_path_ready: false,
        input_path_mode: "",
        output_path_mode: "",
      },
      output: {
        receive_uri: "",
        target: "",
        mode: "",
        direct: false,
        bridge: false,
        bridge_reason: "",
        last_error: "",
      },
      zero_copy: {
        ready: false,
        reason: "Backend unavailable",
        blockers: [],
        status: "blocked",
      },
      recent_events: [],
      debug: {
        enabled: false,
        current_stage: "",
        steps: [],
      },
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
