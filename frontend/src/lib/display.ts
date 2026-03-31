function text(value: unknown, fallback = "알 수 없음"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function displayRuntimeStatus(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "running":
      return "실행 중";
    case "prepared":
      return "준비됨";
    case "idle":
      return "대기";
    case "already_running":
      return "이미 실행 중";
    case "reloaded":
      return "다시 불러옴";
    case "backend unavailable":
      return "백엔드 연결 안 됨";
    case "gpu_only_blocked":
      return "GPU-only 차단";
    case "gpu_only_input_unavailable":
      return "GPU 입력 경로 차단";
    case "gpu_only_output_blocked":
      return "GPU 출력 경로 차단";
    case "gpu_only_path_unavailable":
      return "GPU stitch 경로 차단";
    case "unknown":
      return "알 수 없음";
    default:
      return text(value);
  }
}

export function displayGeometryMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "planar-homography":
      return "평면 호모그래피 (Planar)";
    case "cylindrical-affine":
      return "원통 투영 + Affine (Cylindrical)";
    case "unknown":
      return "알 수 없음";
    default:
      return text(value);
  }
}

export function displaySeamMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "dynamic-path":
      return "동적 경계선";
    case "seam_feather":
    case "feather":
      return "Feather 블렌드";
    case "unknown":
      return "알 수 없음";
    default:
      return text(value);
  }
}

export function displayExposureMode(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "gain-bias":
      return "Gain/Bias 보정";
    case "none":
    case "off":
      return "사용 안 함";
    case "unknown":
      return "알 수 없음";
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
      return "NVENC 직접 경로";
    case "native-nvenc-bridge":
      return "NVENC 브리지";
    case "native-nvenc-unavailable":
      return "NVENC 사용 불가";
    case "none":
      return "사용 안 함";
    case "unknown":
      return "알 수 없음";
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
  return "알 수 없음";
}

export function displayEventType(value: unknown): string {
  switch (String(value ?? "").trim()) {
    case "metrics":
      return "메트릭";
    case "status":
      return "상태";
    case "hello":
      return "시작 알림";
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
      return "점 선택";
    case "review":
    case "calibration-review":
      return "검토";
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
  return "알 수 없음";
}
