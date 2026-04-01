import { useState } from "react";

import { describeProjectActionResult, startProject, stopProject } from "../lib/api";
import { useProjectState } from "../lib/useProjectState";

const STATUS_LABELS: Record<string, string> = {
  idle: "대기",
  starting: "시작 중",
  running: "실행 중",
  blocked: "차단됨",
  error: "오류",
};

const PHASE_LABELS: Record<string, string> = {
  idle: "시작 준비",
  checking_inputs: "입력 확인",
  refreshing_mesh: "메시 갱신",
  preparing_runtime: "런타임 준비",
  starting_runtime: "송출 시작",
  running: "송출 중",
  blocked: "차단됨",
  error: "오류",
};

const START_FLOW = [
  { id: "checking_inputs", label: "입력 확인" },
  { id: "refreshing_mesh", label: "메시 갱신" },
  { id: "preparing_runtime", label: "런타임 준비" },
  { id: "starting_runtime", label: "송출 시작" },
  { id: "running", label: "송출 중" },
] as const;

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

function toneForStatus(status: string): string {
  switch (status) {
    case "running":
      return "success";
    case "starting":
      return "accent";
    case "blocked":
    case "error":
      return "warn";
    default:
      return "neutral";
  }
}

function viewModeForStatus(status: string): "ready" | "starting" | "running" | "blocked" | "error" {
  if (status === "starting") {
    return "starting";
  }
  if (status === "running") {
    return "running";
  }
  if (status === "blocked") {
    return "blocked";
  }
  if (status === "error") {
    return "error";
  }
  return "ready";
}

export function ProjectPage() {
  const { state, loading, refresh } = useProjectState();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("");

  const status = String(state.status || "idle").trim().toLowerCase() || "idle";
  const startPhase = String(state.start_phase || status).trim().toLowerCase() || status;
  const viewMode = viewModeForStatus(status);
  const showDetails = viewMode === "blocked" || viewMode === "error";
  const statusLabel = STATUS_LABELS[status] || text(state.status, "Unknown");
  const phaseLabel = PHASE_LABELS[startPhase] || text(state.start_phase, "Ready");
  const statusMessage =
    text(state.status_message, "") ||
    (state.running
      ? "프로젝트가 실행 중입니다. 외부 플레이어에서 파노라마 출력만 확인하면 됩니다."
      : "Start Project를 누르면 필요한 경우 메시를 자동으로 다시 만들고 바로 송출을 시작합니다.");

  const heading =
    viewMode === "running"
      ? "프로젝트가 실행 중입니다"
      : viewMode === "starting"
        ? "프로젝트를 시작하는 중입니다"
        : viewMode === "blocked"
          ? "시작 전에 확인이 필요합니다"
          : viewMode === "error"
            ? "프로젝트 시작 중 오류가 발생했습니다"
            : "프로젝트 시작 준비 완료";

  const lead =
    viewMode === "running"
      ? "이제 외부 플레이어에서 UDP 주소를 열어 stitched 결과만 확인하면 됩니다."
      : viewMode === "starting"
        ? "같은 화면 안에서 진행 상태만 바뀝니다. 완료되면 자동으로 실행 상태로 전환됩니다."
        : viewMode === "blocked"
          ? "입력 설정이나 메시 상태 때문에 시작이 차단되었습니다. 아래 사유만 확인하면 됩니다."
          : viewMode === "error"
            ? "아래 오류를 확인한 뒤 다시 Start Project를 눌러 재시도할 수 있습니다."
            : "이 화면 하나만 사용합니다. 다른 탭이나 페이지로 이동할 필요 없이 여기서 시작하고 멈추면 됩니다.";

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionStatus(`${label} 진행 중...`);
    try {
      const result = await action();
      setActionStatus(describeProjectActionResult(result));
      await refresh();
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : String(error));
      await refresh();
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <main className="project-shell">
      <section className="project-card">
        <div className="project-header">
          <div className="project-copy">
            <span className="project-eyebrow">Hogak Panorama</span>
            <h1>{heading}</h1>
            <p>{lead}</p>
          </div>
          <div className={`status-badge ${toneForStatus(status)}`}>
            <span className="status-badge-label">상태</span>
            <strong>{loading ? "불러오는 중" : statusLabel}</strong>
          </div>
        </div>

        <div className="project-body">
          <section className="project-main">
            <div className="phase-panel">
              <span className="phase-label">현재 단계</span>
              <strong>{phaseLabel}</strong>
              <p>{statusMessage}</p>
              {actionStatus ? <div className="action-note">{actionStatus}</div> : null}
            </div>

            <div className={`stage-panel ${viewMode}`}>
              {viewMode === "ready" ? (
                <div className="stage-copy">
                  <h2>Start Project 한 번으로 처리됩니다</h2>
                  <p>입력 확인, 필요 시 mesh-refresh, 런타임 준비, 송출 시작까지 자동으로 진행합니다.</p>
                  <ul className="stage-list">
                    <li>현재 기본 geometry는 `virtual-center-rectilinear-mesh` 입니다.</li>
                    <li>메시가 없거나 오래됐으면 내부적으로 자동 갱신합니다.</li>
                    <li>성공하면 외부 플레이어에서 UDP 주소를 바로 열 수 있습니다.</li>
                  </ul>
                </div>
              ) : null}

              {viewMode === "starting" ? (
                <div className="stage-copy">
                  <h2>자동 시작 진행 상황</h2>
                  <div className="progress-list" role="list" aria-label="Project start progress">
                    {START_FLOW.map((step, index) => {
                      const currentIndex = START_FLOW.findIndex((item) => item.id === startPhase);
                      const isDone = currentIndex > index || startPhase === "running";
                      const isCurrent = step.id === startPhase || (startPhase === "running" && step.id === "running");
                      return (
                        <div
                          key={step.id}
                          className={`progress-item ${isDone ? "done" : ""} ${isCurrent ? "current" : ""}`}
                          role="listitem"
                        >
                          <span className="progress-dot" aria-hidden="true" />
                          <div>
                            <strong>{step.label}</strong>
                            <p>
                              {isCurrent
                                ? "지금 이 단계를 진행하고 있습니다."
                                : isDone
                                  ? "완료되었습니다."
                                  : "곧 이어서 진행됩니다."}
                            </p>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {viewMode === "running" ? (
                <div className="stage-copy">
                  <h2>이제 stitched 출력만 확인하면 됩니다</h2>
                  <p>프로젝트는 이미 시작되었습니다. 외부 플레이어에서 아래 UDP 주소를 열고 결과만 확인하세요.</p>
                </div>
              ) : null}

              {viewMode === "blocked" || viewMode === "error" ? (
                <div className="stage-copy">
                  <h2>{viewMode === "blocked" ? "지금 막힌 이유" : "오류 내용"}</h2>
                  <p>{text(state.blocker_reason || state.status_message, "원인을 확인할 수 없습니다.")}</p>
                </div>
              ) : null}
            </div>

            <div className="cta-row">
              <button
                className="primary-cta"
                disabled={busyAction !== null || !state.can_start}
                onClick={() => void runAction("Start Project", () => startProject())}
                type="button"
              >
                Start Project
              </button>
              <button
                className="secondary-cta"
                disabled={busyAction !== null || !state.can_stop}
                onClick={() => void runAction("Stop Project", () => stopProject())}
                type="button"
              >
                Stop Project
              </button>
            </div>

            <div className="output-panel">
              <span className="output-label">외부 플레이어 주소</span>
              <code>{text(state.output_receive_uri, "udp://@:24000")}</code>
              <p>프로젝트가 실행 중 상태가 되면 외부 플레이어에서 이 주소를 열면 됩니다.</p>
            </div>
          </section>

          <details className="details-panel" open={showDetails}>
            <summary>세부 정보</summary>
            <dl className="details-grid">
              <div>
                <dt>활성 모델</dt>
                <dd>{text(state.runtime_active_model, "not ready")}</dd>
              </div>
              <div>
                <dt>Residual</dt>
                <dd>{text(state.geometry_residual_model, "not ready")}</dd>
              </div>
              <div>
                <dt>Artifact 경로</dt>
                <dd>{text(state.runtime_active_artifact_path, "not ready")}</dd>
              </div>
              <div>
                <dt>체크섬</dt>
                <dd>{text(state.runtime_artifact_checksum, "not ready")}</dd>
              </div>
              <div>
                <dt>시작 가능</dt>
                <dd>{state.runtime_launch_ready ? "예" : "아니오"}</dd>
              </div>
              <div>
                <dt>차단 사유</dt>
                <dd>{text(state.runtime_launch_ready_reason, "not ready")}</dd>
              </div>
              <div>
                <dt>GPU path</dt>
                <dd>{text(state.gpu_path_mode, "unknown")}</dd>
              </div>
              <div>
                <dt>GPU 준비</dt>
                <dd>{state.gpu_path_ready ? "예" : "아니오"}</dd>
              </div>
              <div>
                <dt>Fallback 사용</dt>
                <dd>{state.fallback_used ? "예" : "아니오"}</dd>
              </div>
              <div>
                <dt>현재 막힘</dt>
                <dd>{text(state.blocker_reason, "없음")}</dd>
              </div>
            </dl>
          </details>
        </div>
      </section>
    </main>
  );
}
