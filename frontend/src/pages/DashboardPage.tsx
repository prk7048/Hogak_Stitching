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
  displayOutputRuntimeMode,
  displayRuntimeStatus,
  displaySeamMode,
  displayStreamState,
} from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function DashboardPage() {
  const { state, events, preview, streamState, refreshPreview, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("준비되었습니다.");
  const probeReceiveUri = outputReceiveUri(state.output_target);
  const transmitReceiveUri = outputReceiveUri(state.production_output_target);
  const outputDropCount = Number(state.production_output_frames_dropped ?? 0);
  const reuseCount = Number(state.reused_count ?? 0);
  const freshnessWaits = Number(state.wait_paired_fresh_count ?? 0);
  const nextAction = state.running
    ? "메인 출력이 송출 중입니다. 외부 플레이어에서 Transmit 수신 주소를 확인하세요."
    : state.prepared
      ? "런타임 준비가 끝났습니다. 미리보기가 올바르면 시작하세요."
      : "필요하면 캘리브레이션을 마친 뒤 여기에서 런타임을 준비하세요.";
  const healthMessage =
    outputDropCount > 0
      ? `메인 출력에서 ${outputDropCount} 프레임이 드롭되었습니다. Transmit 경로를 확인하세요.`
      : reuseCount > 0 || freshnessWaits > 0
        ? `신선도 제어가 동작 중입니다. 재사용 ${reuseCount}회, fresh 대기 ${freshnessWaits}회입니다.`
        : "런타임 상태가 안정적입니다. 미리보기와 메인 출력을 점검할 수 있습니다.";

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
        title="스티치 스트림을 준비하고 시작하기"
        description="주요 운영 동선에서 런타임을 준비하고, 시작 또는 중지하며, 미리보기를 확인하고, 필요하면 메인 출력 수신 주소로 이동합니다."
        status={
          <>
            <strong>{nextAction}</strong>
            <span>{actionStatus}</span>
          </>
        }
        actions={
          <>
            <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("준비", () => prepareRuntime())} type="button">
              준비
            </button>
            <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("시작", () => startRuntime())} type="button">
              시작
            </button>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("중지", () => stopRuntime())} type="button">
              중지
            </button>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={refreshPreview} type="button">
              미리보기 새로고침
            </button>
          </>
        }
      />

      <section className={`status-strip${outputDropCount > 0 ? " warn" : ""}`}>
        <div className="status-strip-title">운영 미리보기 전용</div>
        <div className="status-strip-body">
          아래 큰 이미지는 Probe JPEG 미리보기입니다. 실제 stitched 출력은 Transmit 수신 URI 또는 외부 플레이어에서
          별도로 확인해야 합니다.
        </div>
        <div className="status-strip-footnote">{healthMessage}</div>
      </section>

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="런타임 상태" value={displayRuntimeStatus(state.status ?? "unknown")} detail={displayRuntimeStatus(state.running ? "running" : state.prepared ? "prepared" : "idle")} tone="accent" />
        <MetricCard label="정렬 방식" value={displayGeometryMode(state.geometry_mode ?? "planar-homography")} detail={`${displaySeamMode(state.seam_mode ?? "feather")} 블렌드`} />
        <MetricCard label="메인 출력" value={displayOutputRuntimeMode(state.production_output_runtime_mode ?? "unknown")} detail={transmitReceiveUri || "수신 URI 없음"} tone={outputDropCount > 0 ? "warn" : "accent"} />
        <MetricCard label="신선도" value={`재사용 ${String(reuseCount)}`} detail={`fresh 대기 ${String(freshnessWaits)}`} tone={reuseCount > 0 || freshnessWaits > 0 ? "warn" : "calm"} />
        <MetricCard label="미리보기 출력" value={displayOutputRuntimeMode(state.output_runtime_mode ?? "unknown")} detail={probeReceiveUri || "수신 URI 없음"} />
        <MetricCard label="이벤트 스트림" value={displayStreamState(streamState)} detail={`최근 이벤트 ${events.length}개`} tone={streamState === "connected" ? "accent" : "warn"} />
      </div>

      <div className="panel-grid panel-grid-main">
        <section className="panel panel-preview">
          <div className="panel-title">운영 미리보기</div>
          <div className="panel-subtitle">빠른 점검용 Probe JPEG 스냅샷입니다. 실제 송출은 아래 메인 출력 주소로 확인하세요.</div>
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
        </section>

        <section className="panel panel-stack">
          <div className="panel-title">운영 요약</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">미리보기 수신 URI</span>
              <span className="definition-value">{probeReceiveUri || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">메인 출력 수신 URI</span>
              <span className="definition-value">{transmitReceiveUri || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">메인 출력 드롭 수</span>
              <span className="definition-value">{String(outputDropCount)}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">Probe 드롭 수</span>
              <span className="definition-value">{String(state.output_frames_dropped ?? 0)}</span>
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
          {events.length === 0 ? <div className="muted">런타임 이벤트를 기다리는 중입니다...</div> : null}
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
