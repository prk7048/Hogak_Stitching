import { useState } from "react";
import { Link } from "react-router-dom";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  describeRuntimeActionResult,
  outputReceiveUri,
  prepareRuntime,
  startRuntime,
  stopRuntime,
} from "../lib/api";
import {
  displayEventType,
  displayGeometryMode,
  displayGpuOnlyState,
  displayOutputRuntimeMode,
  displayRuntimeStatus,
  displaySeamMode,
  displayStreamState,
} from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function asBlockers(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => String(item ?? "").trim())
    .filter((item) => item.length > 0);
}

export function DashboardPage() {
  const { state, events, preview, streamState, refreshPreview, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("준비 상태를 확인했습니다.");

  const probeReceiveUri = outputReceiveUri(state.output_target);
  const transmitReceiveUri = outputReceiveUri(state.production_output_target);
  const outputDropCount = Number(state.production_output_frames_dropped ?? 0);
  const reuseCount = Number(state.reused_count ?? 0);
  const freshnessWaits = Number(state.wait_paired_fresh_count ?? 0);
  const gpuOnlyMode = Boolean(state.gpu_only_mode);
  const gpuOnlyReady = Boolean(state.gpu_only_ready);
  const gpuOnlyBlockers = asBlockers(state.gpu_only_blockers);
  const previewDisabled = String(state.output_runtime_mode ?? "").trim() === "none";

  const nextAction = state.running
    ? "메인 출력이 동작 중입니다. 외부 플레이어에서 Transmit 수신 주소를 확인하세요."
    : state.prepared
      ? "런타임 준비가 끝났습니다. 조건이 맞으면 바로 시작할 수 있습니다."
      : "캘리브레이션을 마친 뒤 여기에서 런타임을 준비하고 시작하세요.";

  const healthMessage =
    gpuOnlyMode && !gpuOnlyReady
      ? "GPU-only 조건이 맞지 않아 준비 또는 시작이 차단됩니다."
      : outputDropCount > 0
        ? `메인 출력에서 ${outputDropCount} 프레임이 드롭됐습니다. Transmit 경로를 점검하세요.`
        : reuseCount > 0 || freshnessWaits > 0
          ? `프레임 재사용 ${reuseCount}회, fresh 대기 ${freshnessWaits}회가 기록됐습니다.`
          : "런타임과 출력 경로가 안정 상태입니다.";

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionStatus(`${label} 작업을 실행하는 중입니다...`);
    try {
      const result = await action();
      setActionStatus(`${label}: ${describeRuntimeActionResult(result)}`);
      await refreshRuntime();
      refreshPreview();
    } catch (error) {
      setActionStatus(`${label} 실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="운영"
        title="스티칭 런타임 준비와 시작"
        description="Prepare, Start, Stop과 상태 확인을 한 화면에서 처리합니다. GPU-only 브랜치에서는 메인 송출(Transmit)을 우선하고, Probe 미리보기는 기본 비활성입니다."
        status={
          <>
            <strong>{nextAction}</strong>
            <span>{actionStatus}</span>
          </>
        }
        actions={
          <>
            <button
              className="action-button"
              disabled={busyAction !== null}
              onClick={() => void runAction("준비", () => prepareRuntime())}
              type="button"
            >
              준비
            </button>
            <button
              className="action-button"
              disabled={busyAction !== null}
              onClick={() => void runAction("시작", () => startRuntime())}
              type="button"
            >
              시작
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null}
              onClick={() => void runAction("중지", () => stopRuntime())}
              type="button"
            >
              중지
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null || previewDisabled}
              onClick={refreshPreview}
              type="button"
            >
              미리보기 새로고침
            </button>
          </>
        }
      />

      <section className={`status-strip${outputDropCount > 0 || (gpuOnlyMode && !gpuOnlyReady) ? " warn" : ""}`}>
        <div className="status-strip-title">운영 상태 요약</div>
        <div className="status-strip-body">
          {previewDisabled
            ? "GPU-only 모드에서는 Probe 미리보기를 기본으로 끄고, 메인 송출 성능을 우선합니다."
            : "아래 미리보기는 Probe JPEG입니다. 실제 stitched 출력은 Transmit 수신 URI로 확인해야 합니다."}
        </div>
        <div className="status-strip-footnote">{healthMessage}</div>
      </section>

      <div className="metric-grid metric-grid-compact">
        <MetricCard
          label="런타임 상태 (Runtime)"
          value={displayRuntimeStatus(state.status ?? "unknown")}
          detail={displayRuntimeStatus(state.running ? "running" : state.prepared ? "prepared" : "idle")}
          tone="accent"
        />
        <MetricCard
          label="기하 모드 (Geometry)"
          value={displayGeometryMode(state.geometry_mode ?? "planar-homography")}
          detail={`${displaySeamMode(state.seam_mode ?? "feather")} 블렌드`}
        />
        <MetricCard
          label="GPU-only 준비 상태"
          value={displayGpuOnlyState(state.gpu_only_ready)}
          detail={gpuOnlyMode ? "하드 GPU-only 모드" : "GPU-only 비활성"}
          tone={gpuOnlyMode && !gpuOnlyReady ? "warn" : "accent"}
        />
        <MetricCard
          label="메인 출력 (Transmit)"
          value={displayOutputRuntimeMode(state.production_output_runtime_mode ?? "unknown")}
          detail={transmitReceiveUri || "수신 URI 없음"}
          tone={outputDropCount > 0 ? "warn" : "accent"}
        />
        <MetricCard
          label="프레임 정책"
          value={`재사용 ${String(reuseCount)}회`}
          detail={`fresh 대기 ${String(freshnessWaits)}회`}
          tone={reuseCount > 0 || freshnessWaits > 0 ? "warn" : "calm"}
        />
        <MetricCard
          label="이벤트 스트림"
          value={displayStreamState(streamState)}
          detail={`최근 이벤트 ${events.length}개`}
          tone={streamState === "connected" ? "accent" : "warn"}
        />
      </div>

      {gpuOnlyMode && gpuOnlyBlockers.length > 0 ? (
        <section className="panel">
          <div className="panel-title">GPU-only 차단 사유</div>
          <div className="definition-list">
            {gpuOnlyBlockers.map((blocker, index) => (
              <div className="definition-item" key={`${blocker}-${index}`}>
                <span className="definition-label">차단 {index + 1}</span>
                <span className="definition-value">{blocker}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="panel-grid panel-grid-main">
        <section className="panel panel-preview">
          <div className="panel-title">운영 미리보기</div>
          <div className="panel-subtitle">
            {previewDisabled
              ? "이 브랜치에서는 Probe 출력을 비활성화했습니다. 성능 확인은 외부 플레이어에서 메인 출력으로 진행하세요."
              : "빠른 점검용 Probe JPEG입니다. 실제 송출 품질은 아래 메인 출력 주소로 확인하세요."}
          </div>
          {previewDisabled ? (
            <div className="muted">
              GPU-only 모드에서는 미리보기 경로를 기본으로 끕니다. <code>{transmitReceiveUri || "수신 URI 없음"}</code> 를 외부 플레이어에서 열어 확인하세요.
            </div>
          ) : (
            <img
              className="preview-image"
              src={preview}
              alt="운영 미리보기"
              onError={(event) => {
                event.currentTarget.src =
                  "data:image/svg+xml;utf8," +
                  encodeURIComponent(
                    "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'><rect width='100%' height='100%' fill='#0b0f17'/><text x='50%' y='50%' fill='#d9d2c2' font-size='28' text-anchor='middle'>미리보기를 불러올 수 없습니다</text></svg>",
                  );
              }}
            />
          )}
        </section>

        <section className="panel panel-stack">
          <div className="panel-title">운영 요약</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">메인 출력 수신 URI</span>
              <span className="definition-value">{transmitReceiveUri || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">메인 출력 드롭</span>
              <span className="definition-value">{String(outputDropCount)}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Probe 출력</span>
              <span className="definition-value">
                {previewDisabled ? "비활성 (GPU-only)" : probeReceiveUri || "수신 URI 없음"}
              </span>
            </div>
            <div className="definition-item">
              <span className="definition-label">마지막 GPU 사유</span>
              <span className="definition-value">{String(state.gpu_reason ?? "-")}</span>
            </div>
          </div>
          <Link className="inline-link" to="/outputs">
            출력 안내 자세히 보기
          </Link>
        </section>
      </div>

      <details className="details-panel">
        <summary className="details-summary">상세 이벤트 로그</summary>
        <div className="event-list">
          {events.length === 0 ? <div className="muted">이벤트를 기다리는 중입니다...</div> : null}
          {events.map((event, index) => (
            <div className="event-item" key={`${event.type}-${event.seq ?? index}`}>
              <span className="event-type">{displayEventType(event.type)}</span>
              <span className="event-body">{JSON.stringify(event.payload ?? event.raw ?? {}, null, 0)}</span>
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}
