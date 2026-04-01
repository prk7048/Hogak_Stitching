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

export type RuntimeActionResponse = Record<string, unknown>;

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
  candidates: GeometryBakeoffCandidate[];
};

export type CalibrationPair = {
  index: number;
  label: string;
  left: number[];
  right: number[];
  selected: boolean;
};

export type CalibrationState = {
  current_step: string;
  route: string;
  workflow: {
    current_step: string;
    manual_pair_count: number;
    homography_reference: string;
    show_inliers: boolean;
    bridge_mode: string;
  };
  output_standard_options: string[];
  start: {
    output_standard: string;
    run_calibration_first: boolean;
    open_vlc_low_latency: boolean;
    use_current_homography_enabled: boolean;
    homography: Record<string, unknown>;
  };
  assisted: {
    left_image_url: string;
    right_image_url: string;
    pair_count: number;
    pending_side: string;
    pending_left_point: number[] | null;
    selected_pair_index: number | null;
    pairs: CalibrationPair[];
    compute_enabled: boolean;
  };
  review: {
    preview_image_url: string;
    inlier_image_url: string;
    candidate: Record<string, unknown> | null;
  };
  stitch_review: {
    preview_image_url: string;
    probe_sender_target: string;
    transmit_sender_target: string;
    probe_receive_uri: string;
    transmit_receive_uri: string;
    probe_loopback_only: boolean;
    transmit_loopback_only: boolean;
  };
  recent_events: string[];
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

function emptyCalibrationState(): CalibrationState {
  return {
    current_step: "start",
    route: "/calibration/start",
    workflow: {
      current_step: "start",
      manual_pair_count: 0,
      homography_reference: "raw",
      show_inliers: true,
      bridge_mode: "react-single-surface",
    },
    output_standard_options: [],
    start: {
      output_standard: "",
      run_calibration_first: true,
      open_vlc_low_latency: false,
      use_current_homography_enabled: false,
      homography: {},
    },
    assisted: {
      left_image_url: "",
      right_image_url: "",
      pair_count: 0,
      pending_side: "left",
      pending_left_point: null,
      selected_pair_index: null,
      pairs: [],
      compute_enabled: false,
    },
    review: {
      preview_image_url: "",
      inlier_image_url: "",
      candidate: null,
    },
    stitch_review: {
      preview_image_url: "",
      probe_sender_target: "",
      transmit_sender_target: "",
      probe_receive_uri: "",
      transmit_receive_uri: "",
      probe_loopback_only: false,
      transmit_loopback_only: false,
    },
    recent_events: [],
  };
}

function normalizeCalibrationPair(value: unknown): CalibrationPair | null {
  if (!isRecord(value)) {
    return null;
  }
  const index = Number(value.index);
  if (!Number.isFinite(index)) {
    return null;
  }
  return {
    index,
    label: pickString(value.label, `쌍 ${index + 1}`).replace(/^Pair\s+(\d+)$/i, "쌍 $1"),
    left: Array.isArray(value.left) ? value.left.map((item) => Number(item)) : [],
    right: Array.isArray(value.right) ? value.right.map((item) => Number(item)) : [],
    selected: Boolean(value.selected),
  };
}

function normalizeCalibrationState(value: unknown): CalibrationState {
  const fallback = emptyCalibrationState();
  if (!isRecord(value)) {
    return fallback;
  }

  const workflow = isRecord(value.workflow) ? value.workflow : {};
  const start = isRecord(value.start) ? value.start : {};
  const assisted = isRecord(value.assisted) ? value.assisted : {};
  const review = isRecord(value.review) ? value.review : {};
  const stitchReview = isRecord(value.stitch_review) ? value.stitch_review : {};

  return {
    current_step: pickString(value.current_step, fallback.current_step),
    route: pickString(value.route, fallback.route),
    workflow: {
      current_step: pickString(workflow.current_step, fallback.workflow.current_step),
      manual_pair_count: Number(workflow.manual_pair_count ?? fallback.workflow.manual_pair_count),
      homography_reference: pickString(workflow.homography_reference, fallback.workflow.homography_reference),
      show_inliers: Boolean(workflow.show_inliers ?? fallback.workflow.show_inliers),
      bridge_mode: pickString(workflow.bridge_mode, fallback.workflow.bridge_mode),
    },
    output_standard_options: Array.isArray(value.output_standard_options)
      ? value.output_standard_options.map((item) => pickString(item)).filter(Boolean)
      : fallback.output_standard_options,
    start: {
      output_standard: pickString(start.output_standard, fallback.start.output_standard),
      run_calibration_first: Boolean(start.run_calibration_first ?? fallback.start.run_calibration_first),
      open_vlc_low_latency: Boolean(start.open_vlc_low_latency ?? fallback.start.open_vlc_low_latency),
      use_current_homography_enabled: Boolean(
        start.use_current_homography_enabled ?? fallback.start.use_current_homography_enabled,
      ),
      homography: isRecord(start.homography) ? start.homography : fallback.start.homography,
    },
    assisted: {
      left_image_url: pickString(assisted.left_image_url),
      right_image_url: pickString(assisted.right_image_url),
      pair_count: Number(assisted.pair_count ?? fallback.assisted.pair_count),
      pending_side: pickString(assisted.pending_side, fallback.assisted.pending_side),
      pending_left_point: Array.isArray(assisted.pending_left_point)
        ? assisted.pending_left_point.map((item) => Number(item))
        : null,
      selected_pair_index:
        assisted.selected_pair_index === null || assisted.selected_pair_index === undefined
          ? null
          : Number(assisted.selected_pair_index),
      pairs: Array.isArray(assisted.pairs)
        ? assisted.pairs
            .map((item) => normalizeCalibrationPair(item))
            .filter((item): item is CalibrationPair => item !== null)
        : [],
      compute_enabled: Boolean(assisted.compute_enabled ?? fallback.assisted.compute_enabled),
    },
    review: {
      preview_image_url: pickString(review.preview_image_url),
      inlier_image_url: pickString(review.inlier_image_url),
      candidate: isRecord(review.candidate) ? review.candidate : null,
    },
    stitch_review: {
      preview_image_url: pickString(stitchReview.preview_image_url),
      probe_sender_target: pickString(stitchReview.probe_sender_target),
      transmit_sender_target: pickString(stitchReview.transmit_sender_target),
      probe_receive_uri: pickString(stitchReview.probe_receive_uri),
      transmit_receive_uri: pickString(stitchReview.transmit_receive_uri),
      probe_loopback_only: Boolean(stitchReview.probe_loopback_only),
      transmit_loopback_only: Boolean(stitchReview.transmit_loopback_only),
    },
    recent_events: Array.isArray(value.recent_events) ? value.recent_events.map((item) => pickString(item)).filter(Boolean) : [],
  };
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
    geometry_rollout_status: pickString(item.geometry_rollout_status),
    operator_visible: typeof item.operator_visible === "boolean" ? item.operator_visible : undefined,
    fallback_only: typeof item.fallback_only === "boolean" ? item.fallback_only : undefined,
    compat_only: typeof item.compat_only === "boolean" ? item.compat_only : undefined,
    launch_ready: typeof item.launch_ready === "boolean" ? item.launch_ready : undefined,
    launch_ready_reason: pickString(item.launch_ready_reason),
    output_resolution: Array.isArray(item.output_resolution) ? item.output_resolution.map((value) => Number(value)) : undefined,
    calibration,
    source,
    raw: item,
  };
}

function normalizeBakeoffCandidate(item: unknown): GeometryBakeoffCandidate | null {
  if (!isRecord(item)) {
    return null;
  }
  return {
    model: pickString(item.model),
    global_model: pickString(item.global_model),
    residual_model: pickString(item.residual_model),
    projection_model: pickString(item.projection_model),
    exposure_model: pickString(item.exposure_model),
    seam_model: pickString(item.seam_model),
    blend_model: pickString(item.blend_model),
    crop_model: pickString(item.crop_model),
    good_match_count: Number(item.good_match_count ?? 0),
    inlier_count: Number(item.inlier_count ?? 0),
    mean_reprojection_error_px: Number(item.mean_reprojection_error_px ?? 0),
    vertical_misalignment_p90_px: Number(item.vertical_misalignment_p90_px ?? 0),
    overlap_luma_diff: Number(item.overlap_luma_diff ?? 0),
    seam_visibility_score: Number(item.seam_visibility_score ?? 0),
    right_edge_scale_drift: Number(item.right_edge_scale_drift ?? 0),
    crop_ratio: Number(item.crop_ratio ?? 0),
    mesh_max_displacement_px: Number(item.mesh_max_displacement_px ?? 0),
    mesh_max_local_scale_drift: Number(item.mesh_max_local_scale_drift ?? 0),
    mesh_max_local_rotation_drift: Number(item.mesh_max_local_rotation_drift ?? 0),
    status: pickString(item.status, "unknown"),
    fallback_used: Boolean(item.fallback_used),
    selected: Boolean(item.selected),
    runtime_artifact_path: pickString(item.runtime_artifact_path),
    stitched_preview_url: pickString(item.stitched_preview_url),
    stitched_video_url: pickString(item.stitched_video_url),
    overlap_crop_url: pickString(item.overlap_crop_url),
    seam_debug_url: pickString(item.seam_debug_url),
    video_duration_sec: Number(item.video_duration_sec ?? 0),
    video_fps: Number(item.video_fps ?? 0),
    video_frame_count: Number(item.video_frame_count ?? 0),
  };
}

function normalizeBakeoffState(item: unknown): GeometryBakeoffState {
  if (!isRecord(item)) {
    return {
      status: "idle",
      session_id: "",
      bundle_dir: "",
      selected_candidate_model: "",
      promoted_candidate_model: "",
      runtime_active_artifact_path: "",
      candidates: [],
    };
  }
  return {
    status: pickString(item.status, "idle"),
    session_id: pickString(item.session_id),
    bundle_dir: pickString(item.bundle_dir),
    selected_candidate_model: pickString(item.selected_candidate_model),
    promoted_candidate_model: pickString(item.promoted_candidate_model),
    runtime_active_artifact_path: pickString(item.runtime_active_artifact_path),
    candidates: Array.isArray(item.candidates)
      ? item.candidates.map((candidate) => normalizeBakeoffCandidate(candidate)).filter((candidate): candidate is GeometryBakeoffCandidate => candidate !== null)
      : [],
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

export async function fetchGeometryBakeoffState(): Promise<GeometryBakeoffState> {
  try {
    const payload = await requestJson<unknown>("/api/bakeoff/state");
    return normalizeBakeoffState(payload);
  } catch {
    return normalizeBakeoffState({});
  }
}

export async function runGeometryBakeoff(body?: unknown): Promise<GeometryBakeoffState> {
  const payload = await postJson<unknown>("/api/bakeoff/run", body ?? {});
  return normalizeBakeoffState(payload);
}

export async function selectGeometryBakeoffWinner(body: { bundle_dir: string; model: string }): Promise<GeometryBakeoffState> {
  const payload = await postJson<unknown>("/api/bakeoff/select", body);
  return normalizeBakeoffState(payload);
}

export async function promoteGeometryBakeoffWinner(body: { bundle_dir: string; model?: string }): Promise<GeometryBakeoffState> {
  const payload = await postJson<unknown>("/api/bakeoff/promote", body);
  return normalizeBakeoffState(payload);
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

async function calibrationAction(path: string, body?: unknown): Promise<CalibrationState> {
  const payload = await postJson<Record<string, unknown>>(path, body ?? {});
  const state = isRecord(payload.state) ? payload.state : payload;
  return normalizeCalibrationState(state);
}

export async function fetchCalibrationState(): Promise<CalibrationState> {
  try {
    return normalizeCalibrationState(await requestJson<unknown>("/api/calibration/session/state"));
  } catch {
    return emptyCalibrationState();
  }
}

export async function startCalibrationSession(body: {
  output_standard?: string;
  run_calibration_first?: boolean;
  open_vlc_low_latency?: boolean;
}): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/session/start", body);
}

export async function refreshCalibrationFrames(): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/frames/refresh");
}

export async function addCalibrationPair(body: { slot: "left" | "right"; x: number; y: number }): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/pairs", body);
}

export async function selectCalibrationPair(index: number): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/pairs/select", { index });
}

export async function undoCalibrationPair(): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/pairs/undo");
}

export async function deleteCalibrationPair(): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/pairs/delete");
}

export async function clearCalibrationPairs(): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/pairs/clear");
}

export async function computeCalibrationCandidate(): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/candidate/compute");
}

export async function fetchCalibrationReview(): Promise<CalibrationState> {
  const payload = await requestJson<Record<string, unknown>>("/api/calibration/review");
  const state = isRecord(payload.state) ? payload.state : payload;
  return normalizeCalibrationState(state);
}

export async function acceptCalibrationReview(): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/review/accept");
}

export async function cancelCalibrationReview(): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/review/cancel");
}

export async function fetchStitchReview(): Promise<CalibrationState> {
  const payload = await requestJson<Record<string, unknown>>("/api/calibration/stitch-review");
  const state = isRecord(payload.state) ? payload.state : payload;
  return normalizeCalibrationState(state);
}

export async function useCurrentHomography(body: {
  output_standard?: string;
  run_calibration_first?: boolean;
  open_vlc_low_latency?: boolean;
}): Promise<CalibrationState> {
  return calibrationAction("/api/calibration/use-current", body);
}

export function calibrationImageUrl(path: string): string {
  return path ? joinPath(path) : "";
}

export function describeRuntimeActionResult(result: unknown): string {
  if (!isRecord(result)) {
    return "작업을 완료했습니다.";
  }
  const message = pickString(result.message, result.detail, result.error);
  if (message) {
    return message;
  }
  if (result.ok === false) {
    return "요청에 실패했습니다.";
  }
  if (result.ok === true) {
    const state = isRecord(result.state) ? result.state : null;
    if (state) {
      return `정상 처리: ${pickString(state.status, state.state, "상태가 업데이트되었습니다.")}`;
    }
    const plan = isRecord(result.plan) ? result.plan : null;
    if (plan) {
      return `정상 처리: ${pickString(plan.geometry_artifact_path, plan.output_runtime_mode, "계획이 업데이트되었습니다.")}`;
    }
    return "정상 처리";
  }
  return "작업을 완료했습니다.";
}

export function runtimeEventsUrl(): string {
  return joinPath("/api/runtime/events");
}

export function previewUrl(version: number): string {
  return joinPath(`/api/runtime/preview.jpg?ts=${version}`);
}

export function outputReceiveUri(target: unknown): string {
  const text = pickString(target);
  if (!text) {
    return "";
  }
  if (text.startsWith("udp://")) {
    const endpoint = text.split("?", 1)[0].slice("udp://".length);
    const hostPort = endpoint.startsWith("@") ? endpoint.slice(1) : endpoint;
    const separator = hostPort.lastIndexOf(":");
    if (separator < 0) {
      return text;
    }
    const port = hostPort.slice(separator + 1).trim();
    return port ? `udp://@:${port}?fifo_size=${8 * 1024 * 1024}&overrun_nonfatal=1` : text;
  }
  return text;
}

function withUdpReceiveSafetyOptions(uri: string): string {
  if (!uri.startsWith("udp://")) {
    return uri;
  }
  let safeUri = uri;
  if (!safeUri.includes("fifo_size=")) {
    const separator = safeUri.includes("?") ? "&" : "?";
    safeUri = `${safeUri}${separator}fifo_size=${8 * 1024 * 1024}`;
  }
  if (!safeUri.includes("overrun_nonfatal=")) {
    const separator = safeUri.includes("?") ? "&" : "?";
    safeUri = `${safeUri}${separator}overrun_nonfatal=1`;
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
  if (!text) {
    return "";
  }
  if (text.startsWith("udp://")) {
    const endpoint = text.split("?", 1)[0].slice("udp://".length);
    const hostPort = endpoint.startsWith("@") ? endpoint.slice(1) : endpoint;
    const separator = hostPort.lastIndexOf(":");
    const host = (separator >= 0 ? hostPort.slice(0, separator) : hostPort).trim().toLowerCase();
    if (host === "127.0.0.1" || host === "localhost" || host === "::1") {
      return "루프백 전용: 같은 Windows PC에서 VLC 또는 ffplay로 열어야 합니다.";
    }
    if (host) {
      return `원격 수신기는 ${host} 주소로 접근 가능해야 합니다.`;
    }
  }
  return "";
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
