function text(value: unknown, fallback = "Unavailable"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function displayRuntimeStatus(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "running":
      return "Running";
    case "prepared":
      return "Prepared";
    case "preview_ready":
      return "Preview ready";
    case "idle":
      return "Idle";
    case "already_running":
      return "Already running";
    case "reloaded":
      return "Reloaded";
    case "backend unavailable":
      return "Backend unavailable";
    default:
      return text(value);
  }
}

export function displayGeometryMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "virtual-center-rectilinear-mesh":
      return "Virtual-Center Rectilinear + Mesh";
    case "virtual-center-rectilinear-rigid":
      return "Virtual-Center Rectilinear + Rigid";
    case "virtual-center-rectilinear":
      return "Virtual-Center Rectilinear";
    case "planar-homography":
      return "Planar Homography";
    case "cylindrical-affine":
      return "Cylindrical Affine";
    default:
      return text(value);
  }
}

export function displayOutputRuntimeMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "native-nvenc-direct":
      return "NVENC Direct";
    case "native-nvenc-bridge":
      return "NVENC Bridge";
    case "gpu-direct":
      return "GPU Direct";
    case "ffmpeg":
      return "FFmpeg";
    case "none":
      return "Disabled";
    default:
      return text(value);
  }
}

export function displayStreamState(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "connected":
      return "Connected";
    case "connecting":
      return "Connecting";
    case "offline":
      return "Offline";
    default:
      return text(value);
  }
}

export function displayBooleanState(value: unknown): string {
  if (value === true) {
    return "Yes";
  }
  if (value === false) {
    return "No";
  }
  return "Unavailable";
}
