import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  apiUrl,
  describeRuntimeActionResult,
  outputReceiveUri,
  previewAlignRuntime,
  startRuntime,
  stopRuntime,
} from "../lib/api";
import {
  displayEventType,
  displayGeometryMode,
  displayOutputRuntimeMode,
  displayRuntimeStatus,
  displayStreamState,
} from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function asBlockers(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item ?? "").trim()).filter((item) => item.length > 0);
}

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

function previewFallbackSvg(label: string): string {
  return (
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'><rect width='100%' height='100%' fill='#0b0f17'/><text x='50%' y='50%' fill='#d9d2c2' font-size='28' text-anchor='middle'>${label}</text></svg>`,
    )
  );
}

function PreviewImage(props: { src: string; alt: string }) {
  return (
    <img
      className="preview-image"
      src={props.src}
      alt={props.alt}
      onError={(event) => {
        event.currentTarget.src = previewFallbackSvg("Preview unavailable");
      }}
    />
  );
}

export function DashboardPage() {
  const { state, events, previewVersion, streamState, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState(
    "먼저 Bakeoff에서 winner를 선택하고, 실제 runtime에 올릴 수 있는 상태인지 확인한 뒤 정렬 미리보기와 송출을 진행하세요.",
  );

  const gpuOnlyMode = Boolean(state.gpu_only_mode);
  const gpuOnlyReady = Boolean(state.gpu_only_ready);
  const gpuOnlyBlockers = asBlockers(state.gpu_only_blockers);
  const gpuPathMode = text(state.gpu_path_mode, "unknown");
  const gpuPathReady = Boolean(state.gpu_path_ready);

  const bakeoffSelectedModel = text(state.bakeoff_selected_model, "-");
  const promotedRuntimeModel = text(state.promoted_runtime_model, "-");
  const runtimeActiveModel = text(state.runtime_active_model ?? state.geometry_artifact_model ?? state.geometry_mode, "-");
  const runtimeResidualModel = text(state.geometry_residual_model, "-");
  const runtimeArtifactPath = text(state.runtime_active_artifact_path ?? state.geometry_artifact_path, "not prepared");
  const runtimeLaunchReady = Boolean(state.runtime_launch_ready ?? state.launch_ready);
  const runtimeLaunchReason = text(state.runtime_launch_ready_reason ?? state.launch_ready_reason, "-");

  const promotionAttempted = Boolean(state.promotion_attempted);
  const promotionSucceeded = Boolean(state.promotion_succeeded);
  const promotionBlockerReason = text(state.promotion_blocker_reason, "");

  const outputDropCount = Number(state.production_output_frames_dropped ?? 0);
  const reuseCount = Number(state.reused_count ?? 0);
  const freshnessWaits = Number(state.wait_paired_fresh_count ?? 0);
  const transmitReceiveUri = outputReceiveUri(state.production_output_target);

  const alignmentPreviewReady = Boolean(state.alignment_preview_ready ?? state.start_preview_ready);
  const leftPreviewUrl = text(state.alignment_preview_left_url ?? state.start_preview_left_url, "");
  const rightPreviewUrl = text(state.alignment_preview_right_url ?? state.start_preview_right_url, "");
  const stitchedPreviewUrl = text(state.alignment_preview_stitched_url ?? state.start_preview_stitched_url, "");
  const leftPreviewSrc = leftPreviewUrl ? apiUrl(`${leftPreviewUrl}?ts=${previewVersion}`) : "";
  const rightPreviewSrc = rightPreviewUrl ? apiUrl(`${rightPreviewUrl}?ts=${previewVersion}`) : "";
  const stitchedPreviewSrc = stitchedPreviewUrl ? apiUrl(`${stitchedPreviewUrl}?ts=${previewVersion}`) : "";

  const running = Boolean(state.running);
  const showAlignmentPreview = alignmentPreviewReady && !running;
  const showStitchedPreview = Boolean(stitchedPreviewSrc) && running;

  const stage = running
    ? "송출 중"
    : !bakeoffSelectedModel || bakeoffSelectedModel === "-"
      ? "Bakeoff 필요"
      : !promotedRuntimeModel || promotedRuntimeModel === "-"
        ? "후보 선택됨"
        : runtimeLaunchReady
          ? "런타임 승격됨"
          : "승격 대기";

  const nextAction = running
    ? `외부 플레이어에서 ${transmitReceiveUri || "수신 URI"}를 열어 실제 송출을 확인하세요.`
    : !runtimeLaunchReady
      ? "아직 runtime에 올릴 수 있는 geometry가 준비되지 않았습니다. Bakeoff winner와 승격 상태를 먼저 확인하세요."
      : showAlignmentPreview
        ? "정렬 미리보기가 준비됐습니다. 정렬이 괜찮으면 송출 시작을 누르세요."
        : "먼저 정렬 미리보기로 virtual-center 정렬 상태를 확인한 뒤, 송출 시작으로 실제 runtime을 실행하세요.";

  const healthMessage =
    gpuOnlyMode && !gpuOnlyReady
      ? "GPU-only launch가 차단되어 있습니다. blocker를 먼저 해결하세요."
      : promotionAttempted && !promotionSucceeded
        ? `winner는 선택됐지만 runtime 승격은 막혀 있습니다. ${promotionBlockerReason || "unknown blocker"}`
        : !gpuPathReady
          ? `GPU path가 fully ready가 아닙니다: ${gpuPathMode}`
          : outputDropCount > 0
            ? `Transmit에서 ${outputDropCount} 프레임이 드롭됐습니다. 출력 경로를 먼저 점검하세요.`
            : reuseCount > 0 || freshnessWaits > 0
              ? `Pair reuse=${reuseCount}, fresh waits=${freshnessWaits}. 입력 동기 상태를 같이 확인하세요.`
              : "현재 화면에서는 bakeoff 선택 모델, 승격 모델, 실제 active 모델을 분리해서 보여줍니다.";

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionStatus(`${label} 진행 중...`);
    try {
      const result = await action();
      setActionStatus(`${label}: ${describeRuntimeActionResult(result)}`);
      await refreshRuntime();
    } catch (error) {
      setActionStatus(`${label} 실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="Run"
        title="승격된 geometry로 송출"
        description="이 화면에서는 bakeoff winner가 실제 runtime에 올라갈 수 있는지 확인하고, 정렬 미리보기와 송출 시작을 분리해서 진행합니다."
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
              disabled={busyAction !== null || running || !runtimeLaunchReady}
              onClick={() => void runAction("정렬 미리보기", () => previewAlignRuntime())}
              type="button"
            >
              정렬 미리보기
            </button>
            <button
              className="action-button"
              disabled={busyAction !== null || running || !runtimeLaunchReady}
              onClick={() => void runAction("송출 시작", () => startRuntime())}
              type="button"
            >
              송출 시작
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null || !running}
              onClick={() => void runAction("송출 중지", () => stopRuntime())}
              type="button"
            >
              송출 중지
            </button>
          </>
        }
      />

      <section className={`status-strip${outputDropCount > 0 || (gpuOnlyMode && !gpuOnlyReady) ? " warn" : ""}`}>
        <div className="status-strip-title">Run summary</div>
        <div className="status-strip-body">
          {showAlignmentPreview
            ? "좌우 입력을 virtual-center plane으로 각각 정렬한 결과를 먼저 보여줍니다. 정렬이 괜찮으면 송출 시작을 누르세요."
            : showStitchedPreview
              ? "송출 중에는 stitched snapshot을 보여줍니다. 실제 지연과 품질은 외부 플레이어에서 transmit URI로 확인하세요."
              : "정렬 미리보기를 실행하면 좌우 입력의 정렬 결과가 표시됩니다."}
        </div>
        <div className="status-strip-footnote">{healthMessage}</div>
      </section>

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="운영 단계" value={stage} detail={displayRuntimeStatus(state.status ?? "unknown")} tone="accent" />
        <MetricCard label="Bakeoff winner" value={bakeoffSelectedModel} detail="selection truth" />
        <MetricCard
          label="승격된 runtime"
          value={promotedRuntimeModel}
          detail="promotion truth"
          tone={promotedRuntimeModel !== "-" ? "accent" : "calm"}
        />
        <MetricCard
          label="실제 active 모델"
          value={displayGeometryMode(runtimeActiveModel)}
          detail={`${runtimeArtifactPath} / residual=${runtimeResidualModel}`}
          tone="accent"
        />
        <MetricCard
          label="실행 준비"
          value={runtimeLaunchReady ? "ready" : "blocked"}
          detail={runtimeLaunchReason}
          tone={runtimeLaunchReady ? "accent" : "warn"}
        />
        <MetricCard
          label="GPU 경로"
          value={displayOutputRuntimeMode(gpuPathMode)}
          detail={gpuPathReady ? "ready" : "not ready"}
          tone={gpuPathReady ? "accent" : "warn"}
        />
        <MetricCard
          label="Transmit"
          value={displayOutputRuntimeMode(state.production_output_runtime_mode ?? "unknown")}
          detail={transmitReceiveUri || "missing receive URI"}
          tone={outputDropCount > 0 ? "warn" : "accent"}
        />
        <MetricCard
          label="이벤트 스트림"
          value={displayStreamState(streamState)}
          detail={`events=${events.length}`}
          tone={streamState === "connected" ? "accent" : "warn"}
        />
      </div>

      {gpuOnlyMode && gpuOnlyBlockers.length > 0 ? (
        <section className="panel">
          <div className="panel-title">GPU-only blockers</div>
          <div className="definition-list">
            {gpuOnlyBlockers.map((blocker, index) => (
              <div className="definition-item" key={`${blocker}-${index}`}>
                <span className="definition-label">Blocker {index + 1}</span>
                <span className="definition-value">{blocker}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="panel-grid calibration-grid">
        <section className="panel">
          <div className="panel-title">왼쪽 정렬 미리보기</div>
          {showAlignmentPreview && leftPreviewSrc ? (
            <PreviewImage src={leftPreviewSrc} alt="Left aligned preview" />
          ) : (
            <div className="muted">정렬 미리보기를 실행하면 여기에 표시됩니다.</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">오른쪽 정렬 미리보기</div>
          {showAlignmentPreview && rightPreviewSrc ? (
            <PreviewImage src={rightPreviewSrc} alt="Right aligned preview" />
          ) : (
            <div className="muted">정렬 미리보기를 실행하면 여기에 표시됩니다.</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">Stitched preview</div>
          {showStitchedPreview ? (
            <PreviewImage src={stitchedPreviewSrc} alt="Stitched preview" />
          ) : showAlignmentPreview && stitchedPreviewSrc ? (
            <PreviewImage src={stitchedPreviewSrc} alt="Aligned stitched preview" />
          ) : (
            <div className="muted">정렬 미리보기 또는 송출 시작 이후에 결과가 표시됩니다.</div>
          )}
        </section>
      </div>

      <section className="panel">
        <div className="panel-title">최근 이벤트</div>
        {events.length === 0 ? (
          <div className="muted">아직 수신한 이벤트가 없습니다.</div>
        ) : (
          <div className="event-list">
            {events.slice(0, 8).map((event, index) => (
              <article className="event-item" key={`${event.type}-${event.seq ?? index}`}>
                <div className="event-header">
                  <strong>{displayEventType(event.type)}</strong>
                  <span>{event.seq ?? "-"}</span>
                </div>
                <pre className="event-payload">{JSON.stringify(event.raw ?? event.payload ?? {}, null, 2)}</pre>
              </article>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
