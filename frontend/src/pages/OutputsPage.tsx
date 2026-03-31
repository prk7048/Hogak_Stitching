import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ffplayReceiveExample, outputReachabilityHint, outputReceiveUri } from "../lib/api";
import { displayOutputRuntimeMode } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function OutputsPage() {
  const { state } = useRuntimeFeed();
  const probeTarget = String(state.output_target ?? "");
  const transmitTarget = String(state.production_output_target ?? "");
  const probeReceiveUri = outputReceiveUri(probeTarget);
  const transmitReceiveUri = outputReceiveUri(transmitTarget);
  const probeReceiveExample = ffplayReceiveExample(probeTarget);
  const transmitReceiveExample = ffplayReceiveExample(transmitTarget);
  const probeReachability = outputReachabilityHint(probeTarget);
  const transmitReachability = outputReachabilityHint(transmitTarget);

  return (
    <section className="page">
      <PageHeader
        eyebrow="운영 / 출력"
        title="메인 출력과 미리보기 출력 경로"
        description="이 페이지는 수신 안내 전용입니다. 런타임 시작과 중지는 운영 대시보드에서만 처리해 주요 동선이 분산되지 않도록 합니다."
        status={
          <>
            <strong>플레이어에는 송출 대상 주소가 아니라 수신 URI를 넣어야 합니다.</strong>
            <span>같은 PC에서는 보통 VLC 또는 ffplay에서 수신 URI를 바로 열면 됩니다.</span>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="미리보기 출력" value={displayOutputRuntimeMode(state.output_runtime_mode ?? "unknown")} detail={probeReceiveUri || "없음"} tone="accent" />
        <MetricCard label="메인 출력" value={displayOutputRuntimeMode(state.production_output_runtime_mode ?? "unknown")} detail={transmitReceiveUri || "없음"} tone="accent" />
        <MetricCard label="미리보기 드롭" value={String(state.output_frames_dropped ?? 0)} detail={probeTarget || "송출 대상 없음"} />
        <MetricCard
          label="메인 출력 드롭"
          value={String(state.production_output_frames_dropped ?? 0)}
          tone={Number(state.production_output_frames_dropped ?? 0) > 0 ? "warn" : "accent"}
          detail={transmitTarget || "송출 대상 없음"}
        />
      </div>

      <div className="output-grid">
        <section className="panel output-card">
          <div className="panel-title">미리보기 출력</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">송출 대상 주소</span>
              <span className="definition-value">{probeTarget || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">수신 URI</span>
              <span className="definition-value">{probeReceiveUri || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">접근 가능 여부</span>
              <span className="definition-value">{probeReachability || "이 PC에서 수신 대상에 접근 가능한 것으로 보입니다."}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">ffplay 예시</span>
              <span className="definition-value definition-code">{probeReceiveExample || "사용 불가"}</span>
            </div>
          </div>
        </section>

        <section className="panel output-card">
          <div className="panel-title">메인 출력</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">송출 대상 주소</span>
              <span className="definition-value">{transmitTarget || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">수신 URI</span>
              <span className="definition-value">{transmitReceiveUri || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">접근 가능 여부</span>
              <span className="definition-value">{transmitReachability || "이 PC에서 수신 대상에 접근 가능한 것으로 보입니다."}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">ffplay 예시</span>
              <span className="definition-value definition-code">{transmitReceiveExample || "사용 불가"}</span>
            </div>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-title">재생 참고</div>
        <p className="muted">
          UDP 예시는 이미 <code>fifo_size</code> 와 <code>overrun_nonfatal=1</code> 을 포함하고 있어 짧은 수신 버스트로
          재생이 바로 끊어지지 않도록 돕습니다.
        </p>
      </section>
    </section>
  );
}
