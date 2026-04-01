import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ffplayReceiveExample, outputReceiveUri } from "../lib/api";
import { displayOutputRuntimeMode } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function reachabilityHint(target: string): string {
  if (!target.startsWith("udp://")) {
    return "";
  }
  const endpoint = target.split("?", 1)[0].slice("udp://".length);
  const hostPort = endpoint.startsWith("@") ? endpoint.slice(1) : endpoint;
  const separator = hostPort.lastIndexOf(":");
  const host = (separator >= 0 ? hostPort.slice(0, separator) : hostPort).trim().toLowerCase();
  if (host === "127.0.0.1" || host === "localhost" || host === "::1") {
    return "로컬 전용입니다. 같은 PC에서 VLC 또는 ffplay로 여세요.";
  }
  if (host) {
    return `${host} 주소에 도달할 수 있는 수신기에서 열어야 합니다.`;
  }
  return "이 PC에서 수신 대상으로 접근 가능한 것으로 보입니다.";
}

export function OutputsPage() {
  const { state } = useRuntimeFeed();
  const probeTarget = String(state.output_target ?? "");
  const transmitTarget = String(state.production_output_target ?? "");
  const probeReceiveUri = outputReceiveUri(probeTarget);
  const transmitReceiveUri = outputReceiveUri(transmitTarget);
  const probeReceiveExample = ffplayReceiveExample(probeTarget);
  const transmitReceiveExample = ffplayReceiveExample(transmitTarget);
  const probeReachability = reachabilityHint(probeTarget);
  const transmitReachability = reachabilityHint(transmitTarget);
  const probeDisabled = String(state.output_runtime_mode ?? "").trim() === "none";

  return (
    <section className="page">
      <PageHeader
        eyebrow="Run / 출력"
        title="메인 출력과 보조 출력 경로"
        description="이 페이지는 수신 안내 전용입니다. 송출 시작과 중지는 Run 화면에서만 진행하고, GPU 전용 운영에서는 메인 출력(Transmit)만 기본 경로로 사용합니다."
        status={
          <>
            <strong>외부 플레이어에서는 송신 주소가 아니라 수신 URI를 엽니다.</strong>
            <span>같은 PC에서는 보통 VLC 또는 ffplay에 수신 URI를 그대로 넣으면 됩니다.</span>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard
          label="보조 출력 (Probe)"
          value={displayOutputRuntimeMode(state.output_runtime_mode ?? "unknown")}
          detail={probeDisabled ? "GPU 전용 운영에서는 기본 비활성" : probeReceiveUri || "없음"}
          tone={probeDisabled ? "warn" : "accent"}
        />
        <MetricCard
          label="메인 출력 (Transmit)"
          value={displayOutputRuntimeMode(state.production_output_runtime_mode ?? "unknown")}
          detail={transmitReceiveUri || "없음"}
          tone="accent"
        />
        <MetricCard
          label="Probe 드롭"
          value={String(state.output_frames_dropped ?? 0)}
          detail={probeTarget || "송신 주소 없음"}
        />
        <MetricCard
          label="Transmit 드롭"
          value={String(state.production_output_frames_dropped ?? 0)}
          detail={transmitTarget || "송신 주소 없음"}
          tone={Number(state.production_output_frames_dropped ?? 0) > 0 ? "warn" : "accent"}
        />
      </div>

      <div className="output-grid">
        <section className="panel output-card">
          <div className="panel-title">보조 출력 (Probe)</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">송신 주소</span>
              <span className="definition-value">{probeTarget || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">수신 URI</span>
              <span className="definition-value">{probeReceiveUri || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">접속 가능 여부</span>
              <span className="definition-value">
                {probeDisabled
                  ? "GPU 전용 운영에서는 Probe를 기본 비활성로 둡니다."
                  : probeReachability || "이 PC에서 수신 대상으로 접근 가능한 것으로 보입니다."}
              </span>
            </div>
            <div className="definition-item">
              <span className="definition-label">ffplay 예시</span>
              <span className="definition-value definition-code">{probeReceiveExample || "사용 불가"}</span>
            </div>
          </div>
        </section>

        <section className="panel output-card">
          <div className="panel-title">메인 출력 (Transmit)</div>
          <div className="definition-list">
            <div className="definition-item">
              <span className="definition-label">송신 주소</span>
              <span className="definition-value">{transmitTarget || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">수신 URI</span>
              <span className="definition-value">{transmitReceiveUri || "사용 불가"}</span>
            </div>
            <div className="definition-item">
              <span className="definition-label">접속 가능 여부</span>
              <span className="definition-value">
                {transmitReachability || "이 PC에서 수신 대상으로 접근 가능한 것으로 보입니다."}
              </span>
            </div>
            <div className="definition-item">
              <span className="definition-label">ffplay 예시</span>
              <span className="definition-value definition-code">{transmitReceiveExample || "사용 불가"}</span>
            </div>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-title">운영 메모</div>
        <p className="muted">
          GPU 전용 운영에서는 steady-state 성능을 위해 Probe를 끄고 Transmit만 보는 것이 기본입니다. 외부 플레이어에서{" "}
          <code>{transmitReceiveUri || "수신 URI 없음"}</code> 를 열어 실제 송출 프레임과 지연을 확인하세요.
        </p>
      </section>
    </section>
  );
}
