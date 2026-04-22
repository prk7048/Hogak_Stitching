export type DisplayProjectPhase =
  | "idle"
  | "checking_inputs"
  | "refreshing_mesh"
  | "preparing_runtime"
  | "starting_runtime"
  | "confirm_output"
  | "running"
  | "blocked"
  | "error";

const DISPLAY_PHASE_LABELS: Record<DisplayProjectPhase, string> = {
  idle: "Ready",
  checking_inputs: "Checking inputs",
  refreshing_mesh: "Recomputing stitch geometry",
  preparing_runtime: "Preparing runtime",
  starting_runtime: "Starting output",
  confirm_output: "Confirm live output",
  running: "Running",
  blocked: "Blocked",
  error: "Error",
};

const DISPLAY_PHASE_ALIASES: Record<string, DisplayProjectPhase> = {
  idle: "idle",
  check_config: "checking_inputs",
  checking_inputs: "checking_inputs",
  connect_inputs: "refreshing_mesh",
  capture_frames: "refreshing_mesh",
  match_features: "refreshing_mesh",
  solve_geometry: "refreshing_mesh",
  build_artifact: "refreshing_mesh",
  artifact_ready: "refreshing_mesh",
  refreshing_mesh: "refreshing_mesh",
  preparing_runtime: "preparing_runtime",
  launch_runtime: "starting_runtime",
  starting_runtime: "starting_runtime",
  confirm_output: "confirm_output",
  running: "running",
  blocked: "blocked",
  error: "error",
};

export const START_FLOW: ReadonlyArray<{ id: DisplayProjectPhase; label: string }> = [
  { id: "checking_inputs", label: "Check inputs" },
  { id: "refreshing_mesh", label: "Recompute stitch geometry" },
  { id: "preparing_runtime", label: "Prepare runtime" },
  { id: "starting_runtime", label: "Start output" },
  { id: "confirm_output", label: "Confirm live output" },
  { id: "running", label: "Running" },
];

export function normalizeDisplayPhase(phase: unknown, lifecycleState: unknown = ""): DisplayProjectPhase | "" {
  const normalizedPhase = String(phase ?? "").trim().toLowerCase();
  if (normalizedPhase && DISPLAY_PHASE_ALIASES[normalizedPhase]) {
    return DISPLAY_PHASE_ALIASES[normalizedPhase];
  }

  const normalizedStatus = String(lifecycleState ?? "").trim().toLowerCase();
  if (normalizedStatus && DISPLAY_PHASE_ALIASES[normalizedStatus]) {
    return DISPLAY_PHASE_ALIASES[normalizedStatus];
  }

  return "";
}

export function displayPhaseLabel(phase: unknown, lifecycleState: unknown = ""): string {
  const normalizedPhase = normalizeDisplayPhase(phase, lifecycleState);
  if (normalizedPhase) {
    return DISPLAY_PHASE_LABELS[normalizedPhase];
  }

  return String(lifecycleState ?? "").trim().toLowerCase() === "starting" ? "Starting" : "Ready";
}
