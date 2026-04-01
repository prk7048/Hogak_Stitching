export type PreparedPlanSummary = {
  geometry_artifact_path?: string;
  geometry_artifact_model?: string;
  geometry_residual_model?: string;
  geometry_rollout_status?: string;
  output_runtime_mode?: string;
  production_output_runtime_mode?: string;
  sync_pair_mode?: string;
  transmit_target?: string;
  probe_target?: string;
  [key: string]: unknown;
};

export type RuntimeState = {
  running?: boolean;
  prepared?: boolean;
  status?: string;
  last_error?: string;
  prepared_plan?: PreparedPlanSummary | null;
  latest_metrics?: Record<string, unknown>;
  latest_validation?: Record<string, unknown>;
  geometry_mode?: string;
  geometry_artifact_model?: string;
  geometry_artifact_path?: string;
  geometry_residual_model?: string;
  geometry_rollout_status?: string;
  geometry_operator_visible?: boolean;
  geometry_fallback_only?: boolean;
  geometry_compat_only?: boolean;
  launch_ready?: boolean;
  launch_ready_reason?: string;
  runtime_launch_ready?: boolean;
  runtime_launch_ready_reason?: string;
  bakeoff_selected_model?: string;
  promoted_runtime_model?: string;
  runtime_active_model?: string;
  runtime_active_artifact_path?: string;
  promotion_attempted?: boolean;
  promotion_succeeded?: boolean;
  promotion_blocker_reason?: string;
  alignment_preview_ready?: boolean;
  alignment_preview_left_url?: string;
  alignment_preview_right_url?: string;
  alignment_preview_stitched_url?: string;
  start_preview_ready?: boolean;
  start_preview_left_url?: string;
  start_preview_right_url?: string;
  start_preview_stitched_url?: string;
  seam_mode?: string;
  exposure_mode?: string;
  output_runtime_mode?: string;
  production_output_runtime_mode?: string;
  output_target?: string;
  production_output_target?: string;
  production_output_frames_dropped?: number;
  output_frames_dropped?: number;
  reused_count?: number;
  wait_paired_fresh_count?: number;
  gpu_only_mode?: boolean;
  gpu_only_ready?: boolean;
  gpu_only_blockers?: string[];
  gpu_path_mode?: string;
  gpu_path_ready?: boolean;
  gpu_reason?: string;
  strict_fresh?: boolean;
  validation_mode?: string;
  geometry_artifact_checksum?: string;
  event_count?: number;
  [key: string]: unknown;
};

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
  geometry_residual_model?: string;
  geometry_rollout_status?: string;
  operator_visible?: boolean;
  fallback_only?: boolean;
  compat_only?: boolean;
  launch_ready?: boolean;
  launch_ready_reason?: string;
  output_resolution?: number[];
  calibration?: Record<string, unknown>;
  source?: Record<string, unknown>;
  raw?: unknown;
};

export type RuntimeActionResponse = {
  ok?: boolean;
  message?: string;
  detail?: string;
  error?: string;
  preview_ready?: boolean;
  auto_prepared?: boolean;
  auto_calibrated?: boolean;
  state?: RuntimeState;
  prepared?: PreparedPlanSummary | Record<string, unknown>;
  [key: string]: unknown;
};

export type GeometryBakeoffCandidate = {
  model: string;
  global_model: string;
  residual_model: string;
  projection_model: string;
  exposure_model: string;
  seam_model: string;
  blend_model: string;
  crop_model: string;
  good_match_count: number;
  inlier_count: number;
  mean_reprojection_error_px: number;
  vertical_misalignment_p90_px: number;
  overlap_luma_diff: number;
  seam_visibility_score: number;
  right_edge_scale_drift: number;
  crop_ratio: number;
  mesh_max_displacement_px: number;
  mesh_max_local_scale_drift: number;
  mesh_max_local_rotation_drift?: number;
  status: string;
  fallback_used: boolean;
  selected: boolean;
  runtime_artifact_path?: string;
  runtime_launch_ready?: boolean;
  runtime_launch_ready_reason?: string;
  geometry_rollout_status?: string;
  stitched_preview_url?: string;
  stitched_video_url?: string;
  overlap_crop_url?: string;
  seam_debug_url?: string;
  video_duration_sec?: number;
  video_fps?: number;
  video_frame_count?: number;
};

export type GeometryBakeoffState = {
  status: string;
  session_id: string;
  bundle_dir: string;
  selected_candidate_model: string;
  promoted_candidate_model: string;
  runtime_active_artifact_path: string;
  promotion_attempted: boolean;
  promotion_succeeded: boolean;
  promotion_blocker_reason: string;
  candidates: GeometryBakeoffCandidate[];
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

function pickString(...values: unknown[]): string {
  for (const value of values) {
    const text = asString(value, "");
    if (text) {
      return text;
    }
  }
  return "";
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

function asNumberArray(value: unknown): number[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const numbers = value
    .map((item) => asNumber(item, Number.NaN))
    .filter((item) => Number.isFinite(item));
  return numbers.length > 0 ? numbers : undefined;
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

  Object.assign(flattened, value);
  for (const candidate of candidates) {
    if (isRecord(candidate)) {
      Object.assign(flattened, candidate);
    }
  }

  flattened.status = pickString(flattened.status, value.status);
  flattened.last_error = pickString(flattened.last_error, value.last_error);
  flattened.geometry_mode = pickString(flattened.geometry_mode, value.geometry_mode);
  flattened.geometry_artifact_model = pickString(flattened.geometry_artifact_model, value.geometry_artifact_model);
  flattened.geometry_artifact_path = pickString(flattened.geometry_artifact_path, value.geometry_artifact_path);
  flattened.geometry_residual_model = pickString(flattened.geometry_residual_model, value.geometry_residual_model);
  flattened.geometry_rollout_status = pickString(flattened.geometry_rollout_status, value.geometry_rollout_status);
  flattened.launch_ready_reason = pickString(flattened.launch_ready_reason, value.launch_ready_reason);
  flattened.runtime_launch_ready_reason = pickString(
    flattened.runtime_launch_ready_reason,
    value.runtime_launch_ready_reason,
  );
  flattened.bakeoff_selected_model = pickString(flattened.bakeoff_selected_model, value.bakeoff_selected_model);
  flattened.promoted_runtime_model = pickString(flattened.promoted_runtime_model, value.promoted_runtime_model);
  flattened.runtime_active_model = pickString(flattened.runtime_active_model, value.runtime_active_model);
  flattened.runtime_active_artifact_path = pickString(
    flattened.runtime_active_artifact_path,
    value.runtime_active_artifact_path,
  );
  flattened.promotion_blocker_reason = pickString(
    flattened.promotion_blocker_reason,
    value.promotion_blocker_reason,
  );
  flattened.seam_mode = pickString(flattened.seam_mode, value.seam_mode);
  flattened.exposure_mode = pickString(flattened.exposure_mode, value.exposure_mode);
  flattened.output_runtime_mode = pickString(flattened.output_runtime_mode, value.output_runtime_mode);
  flattened.production_output_runtime_mode = pickString(
    flattened.production_output_runtime_mode,
    value.production_output_runtime_mode,
  );
  flattened.output_target = pickString(flattened.output_target, value.output_target);
  flattened.production_output_target = pickString(
    flattened.production_output_target,
    value.production_output_target,
  );
  flattened.gpu_reason = pickString(flattened.gpu_reason, value.gpu_reason);
  flattened.validation_mode = pickString(flattened.validation_mode, value.validation_mode);
  flattened.geometry_artifact_checksum = pickString(
    flattened.geometry_artifact_checksum,
    value.geometry_artifact_checksum,
  );
  flattened.gpu_path_mode = pickString(flattened.gpu_path_mode, value.gpu_path_mode, "unknown");

  flattened.running = asBoolean(flattened.running, false);
  flattened.prepared = asBoolean(flattened.prepared, false);
  flattened.geometry_operator_visible = asBoolean(flattened.geometry_operator_visible, false);
  flattened.geometry_fallback_only = asBoolean(flattened.geometry_fallback_only, false);
  flattened.geometry_compat_only = asBoolean(flattened.geometry_compat_only, false);
  flattened.launch_ready = asBoolean(flattened.launch_ready, false);
  flattened.runtime_launch_ready = asBoolean(flattened.runtime_launch_ready, flattened.launch_ready);
  flattened.promotion_attempted = asBoolean(flattened.promotion_attempted, false);
  flattened.promotion_succeeded = asBoolean(flattened.promotion_succeeded, false);
  flattened.alignment_preview_ready = asBoolean(
    flattened.alignment_preview_ready,
    asBoolean(flattened.start_preview_ready, false),
  );
  flattened.start_preview_ready = asBoolean(flattened.start_preview_ready, false);
  flattened.gpu_only_mode = asBoolean(flattened.gpu_only_mode, false);
  flattened.gpu_only_ready = asBoolean(flattened.gpu_only_ready, false);
  flattened.gpu_path_ready = asBoolean(flattened.gpu_path_ready, false);
  flattened.strict_fresh = asBoolean(flattened.strict_fresh, false);

  flattened.production_output_frames_dropped = asNumber(flattened.production_output_frames_dropped, 0);
  flattened.output_frames_dropped = asNumber(flattened.output_frames_dropped, 0);
  flattened.reused_count = asNumber(flattened.reused_count, 0);
  flattened.wait_paired_fresh_count = asNumber(flattened.wait_paired_fresh_count, 0);
  flattened.event_count = asNumber(flattened.event_count, 0);
  flattened.gpu_only_blockers = asStringArray(flattened.gpu_only_blockers);

  if (isRecord(flattened.prepared_plan)) {
    flattened.prepared_plan = flattened.prepared_plan as PreparedPlanSummary;
  } else {
    flattened.prepared_plan = null;
  }
  flattened.latest_metrics = isRecord(flattened.latest_metrics) ? flattened.latest_metrics : undefined;
  flattened.latest_validation = isRecord(flattened.latest_validation) ? flattened.latest_validation : undefined;

  flattened.alignment_preview_left_url = pickString(
    flattened.alignment_preview_left_url,
    flattened.start_preview_left_url,
    value.alignment_preview_left_url,
    value.start_preview_left_url,
  );
  flattened.alignment_preview_right_url = pickString(
    flattened.alignment_preview_right_url,
    flattened.start_preview_right_url,
    value.alignment_preview_right_url,
    value.start_preview_right_url,
  );
  flattened.alignment_preview_stitched_url = pickString(
    flattened.alignment_preview_stitched_url,
    flattened.start_preview_stitched_url,
    value.alignment_preview_stitched_url,
    value.start_preview_stitched_url,
  );

  return flattened;
}

function normalizeGeometryArtifact(value: unknown, index: number): GeometryArtifactSummary | null {
  if (!isRecord(value)) {
    return null;
  }
  return {
    name: pickString(value.name, `artifact-${index + 1}`),
    path: pickString(value.path),
    artifact_type: pickString(value.artifact_type),
    schema_version: asNumber(value.schema_version, 0) || undefined,
    model: pickString(value.model),
    geometry_model: pickString(value.geometry_model),
    geometry_residual_model: pickString(value.geometry_residual_model),
    geometry_rollout_status: pickString(value.geometry_rollout_status),
    operator_visible: asBoolean(value.operator_visible, false),
    fallback_only: asBoolean(value.fallback_only, false),
    compat_only: asBoolean(value.compat_only, false),
    launch_ready: asBoolean(value.launch_ready, false),
    launch_ready_reason: pickString(value.launch_ready_reason),
    output_resolution: asNumberArray(value.output_resolution),
    calibration: isRecord(value.calibration) ? value.calibration : undefined,
    source: isRecord(value.source) ? value.source : undefined,
    raw: value.raw,
  };
}

function normalizeBakeoffCandidate(value: unknown): GeometryBakeoffCandidate | null {
  if (!isRecord(value)) {
    return null;
  }
  const model = pickString(value.model);
  if (!model) {
    return null;
  }
  return {
    model,
    global_model: pickString(value.global_model),
    residual_model: pickString(value.residual_model),
    projection_model: pickString(value.projection_model),
    exposure_model: pickString(value.exposure_model),
    seam_model: pickString(value.seam_model),
    blend_model: pickString(value.blend_model),
    crop_model: pickString(value.crop_model),
    good_match_count: asNumber(value.good_match_count, 0),
    inlier_count: asNumber(value.inlier_count, 0),
    mean_reprojection_error_px: asNumber(value.mean_reprojection_error_px, 0),
    vertical_misalignment_p90_px: asNumber(value.vertical_misalignment_p90_px, 0),
    overlap_luma_diff: asNumber(value.overlap_luma_diff, 0),
    seam_visibility_score: asNumber(value.seam_visibility_score, 0),
    right_edge_scale_drift: asNumber(value.right_edge_scale_drift, 0),
    crop_ratio: asNumber(value.crop_ratio, 0),
    mesh_max_displacement_px: asNumber(value.mesh_max_displacement_px, 0),
    mesh_max_local_scale_drift: asNumber(value.mesh_max_local_scale_drift, 0),
    mesh_max_local_rotation_drift: asNumber(value.mesh_max_local_rotation_drift, 0),
    status: pickString(value.status, "unknown"),
    fallback_used: asBoolean(value.fallback_used, false),
    selected: asBoolean(value.selected, false),
    runtime_artifact_path: pickString(value.runtime_artifact_path),
    runtime_launch_ready: asBoolean(value.runtime_launch_ready, false),
    runtime_launch_ready_reason: pickString(value.runtime_launch_ready_reason),
    geometry_rollout_status: pickString(value.geometry_rollout_status),
    stitched_preview_url: pickString(value.stitched_preview_url),
    stitched_video_url: pickString(value.stitched_video_url),
    overlap_crop_url: pickString(value.overlap_crop_url),
    seam_debug_url: pickString(value.seam_debug_url),
    video_duration_sec: asNumber(value.video_duration_sec, 0),
    video_fps: asNumber(value.video_fps, 0),
    video_frame_count: asNumber(value.video_frame_count, 0),
  };
}

function normalizeBakeoffState(value: unknown): GeometryBakeoffState {
  if (!isRecord(value)) {
    return {
      status: "idle",
      session_id: "",
      bundle_dir: "",
      selected_candidate_model: "",
      promoted_candidate_model: "",
      runtime_active_artifact_path: "",
      promotion_attempted: false,
      promotion_succeeded: false,
      promotion_blocker_reason: "",
      candidates: [],
    };
  }
  const candidates = Array.isArray(value.candidates)
    ? value.candidates
        .map((candidate) => normalizeBakeoffCandidate(candidate))
        .filter((candidate): candidate is GeometryBakeoffCandidate => candidate !== null)
    : [];
  return {
    status: pickString(value.status, "idle"),
    session_id: pickString(value.session_id),
    bundle_dir: pickString(value.bundle_dir),
    selected_candidate_model: pickString(value.selected_candidate_model),
    promoted_candidate_model: pickString(value.promoted_candidate_model),
    runtime_active_artifact_path: pickString(value.runtime_active_artifact_path),
    promotion_attempted: asBoolean(value.promotion_attempted, false),
    promotion_succeeded: asBoolean(value.promotion_succeeded, false),
    promotion_blocker_reason: pickString(value.promotion_blocker_reason),
    candidates,
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
      type: pickString(parsed.type, fallbackType),
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
      (isRecord(payload) && pickString(payload.detail, payload.error, payload.message)) || `${response.status}`;
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
      geometry_mode: "virtual-center-rectilinear-rigid",
      seam_mode: "unknown",
      exposure_mode: "unknown",
      gpu_path_mode: "unknown",
      gpu_path_ready: false,
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

export async function fetchGeometryBakeoffState(): Promise<GeometryBakeoffState> {
  try {
    return normalizeBakeoffState(await requestJson<unknown>("/api/bakeoff/state"));
  } catch {
    return normalizeBakeoffState({});
  }
}

export async function runGeometryBakeoff(body?: unknown): Promise<GeometryBakeoffState> {
  return normalizeBakeoffState(await postJson<unknown>("/api/bakeoff/run", body ?? {}));
}

export async function useGeometryBakeoffWinner(body: { bundle_dir: string; model: string }): Promise<GeometryBakeoffState> {
  return normalizeBakeoffState(await postJson<unknown>("/api/bakeoff/use-winner", body));
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
    return "작업이 완료되었습니다.";
  }
  const message = pickString(result.message, result.detail, result.error);
  if (message) {
    return message;
  }
  if (result.ok === false) {
    return "요청이 실패했습니다.";
  }
  if (result.ok === true) {
    const state = isRecord(result.state) ? result.state : null;
    if (state) {
      return `정상 처리: ${pickString(state.status, state.state, "상태가 갱신되었습니다.")}`;
    }
    const plan = isRecord(result.prepared) ? result.prepared : null;
    if (plan) {
      return `정상 처리: ${pickString(plan.geometry_artifact_path, plan.output_runtime_mode, "계획이 갱신되었습니다.")}`;
    }
    return "정상 처리";
  }
  return "작업이 완료되었습니다.";
}

export function runtimeEventsUrl(): string {
  return joinPath("/api/runtime/events");
}

export function outputReceiveUri(target: unknown): string {
  const text = pickString(target);
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

function withUdpReceiveSafetyOptions(uri: string): string {
  if (!uri.startsWith("udp://")) {
    return uri;
  }
  let safeUri = uri;
  if (!safeUri.includes("fifo_size=")) {
    safeUri = `${safeUri}${safeUri.includes("?") ? "&" : "?"}fifo_size=${8 * 1024 * 1024}`;
  }
  if (!safeUri.includes("overrun_nonfatal=")) {
    safeUri = `${safeUri}${safeUri.includes("?") ? "&" : "?"}overrun_nonfatal=1`;
  }
  return safeUri;
}

export function ffplayReceiveExample(target: unknown): string {
  const receiveUri = outputReceiveUri(target);
  if (!receiveUri) {
    return "";
  }
  const safeReceiveUri = withUdpReceiveSafetyOptions(receiveUri);
  return `ffplay -fflags nobuffer -flags low_delay -framedrop -sync ext -f mpegts "${safeReceiveUri}"`;
}

export function outputReachabilityHint(target: unknown): string {
  const text = pickString(target);
  if (!text || !text.startsWith("udp://")) {
    return "";
  }
  const endpoint = text.split("?", 1)[0].slice("udp://".length);
  const hostPort = endpoint.startsWith("@") ? endpoint.slice(1) : endpoint;
  const separator = hostPort.lastIndexOf(":");
  const host = (separator >= 0 ? hostPort.slice(0, separator) : hostPort).trim().toLowerCase();
  if (host === "127.0.0.1" || host === "localhost" || host === "::1") {
    return "루프백 전용입니다. 같은 PC에서 VLC 또는 ffplay로 여세요.";
  }
  if (host) {
    return `${host} 주소에 도달할 수 있는 수신기에서 열어야 합니다.`;
  }
  return "";
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
