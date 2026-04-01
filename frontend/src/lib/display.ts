function text(value: unknown, fallback = "정보 없음"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function displayRuntimeStatus(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "running":
      return "송출 중";
    case "prepared":
      return "준비됨";
    case "preview_ready":
    case "start_preview_ready":
      return "정렬 미리보기 준비됨";
    case "idle":
      return "대기 중";
    case "already_running":
      return "이미 실행 중";
    case "reloaded":
      return "재적용됨";
    case "backend unavailable":
      return "백엔드 연결 불가";
    case "gpu_only_blocked":
      return "GPU 전용 실행 차단";
    case "gpu_only_input_unavailable":
      return "GPU 입력 불가";
    case "gpu_only_output_blocked":
      return "GPU 출력 불가";
    case "gpu_only_path_unavailable":
      return "GPU 경로 불가";
    default:
      return text(value);
  }
}

export function displayGeometryMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "planar-homography":
      return "Planar Homography";
    case "cylindrical-affine":
      return "Cylindrical Affine";
    case "virtual-center-rectilinear":
      return "Virtual-Center Rectilinear";
    case "left-anchor-homography":
      return "Left-Anchor Homography";
    case "left-anchor-homography-mesh":
      return "Left-Anchor Homography + Mesh";
    case "virtual-center-rectilinear-rigid":
      return "Virtual-Center Rectilinear + Rigid";
    case "virtual-center-rectilinear-mesh":
      return "Virtual-Center Rectilinear + Mesh";
    default:
      return text(value);
  }
}

export function displaySeamMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "dynamic-path":
      return "동적 seam 경로";
    case "min-cost-seam":
      return "Min-cost seam";
    case "seam_feather":
    case "feather":
    case "narrow-seam-feather":
      return "좁은 seam feather";
    default:
      return text(value);
  }
}

export function displayExposureMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "gain-bias":
    case "gain-bias-luma":
      return "Gain/Bias";
    case "none":
    case "off":
      return "비활성";
    default:
      return text(value);
  }
}

export function displayOutputRuntimeMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "ffmpeg":
      return "FFmpeg";
    case "gpu-direct":
      return "GPU-Direct";
    case "native-nvenc-direct":
      return "NVENC Direct";
    case "native-nvenc-bridge":
      return "NVENC Bridge";
    case "native-nvenc-unavailable":
      return "NVENC 사용 불가";
    case "none":
      return "비활성";
    default:
      return text(value);
  }
}

export function displayStreamState(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "connected":
      return "연결됨";
    case "connecting":
      return "연결 중";
    case "offline":
      return "오프라인";
    default:
      return text(value);
  }
}

export function displayBooleanState(value: unknown): string {
  if (value === true) {
    return "예";
  }
  if (value === false) {
    return "아니오";
  }
  return "정보 없음";
}

export function displayEventType(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "metrics":
      return "메트릭";
    case "status":
      return "상태";
    case "hello":
      return "초기 상태";
    case "stopped":
      return "중지";
    case "error":
      return "오류";
    case "message":
      return "메시지";
    default:
      return text(value, "이벤트");
  }
}

export function displayCalibrationStep(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "start":
      return "시작";
    case "assisted":
    case "assisted-calibration":
      return "점 선택";
    case "review":
    case "calibration-review":
      return "검토";
    case "geometry-compare":
      return "Geometry Bakeoff";
    case "stitch-review":
      return "최종 확인";
    default:
      return text(value);
  }
}

export function displayGpuOnlyState(ready: unknown): string {
  if (ready === true) {
    return "준비 완료";
  }
  if (ready === false) {
    return "차단됨";
  }
  return "정보 없음";
}
