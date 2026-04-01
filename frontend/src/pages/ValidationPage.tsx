import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { describeRuntimeActionResult, validateRuntime } from "../lib/api";
import { displayBooleanState, displayRuntimeStatus } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

export function ValidationPage() {
  const { state, refreshRuntime } = useRuntimeFeed();
  const preparedPlan = state.prepared_plan as Record<string, unknown> | undefined;
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState(
    "아직 validation을 실행하지 않았습니다. 이 화면은 bakeoff winner, 승격된 runtime, 실제 active model을 서로 나눠서 보여줍니다.",
  );

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setValidationResult(`${label} 진행 중...`);
    try {
      const result = await action();
      setValidationResult(`${label}: ${describeRuntimeActionResult(result)}\n${JSON.stringify(result, null, 2)}`);
      await refreshRuntime();
    } catch (error) {
      setValidationResult(`${label} 실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="Validate"
        title="읽기 전용 런타임 검증"
        description="이 화면은 bakeoff winner, 승격된 runtime model, 실제 active runtime model을 각각 별도로 검증합니다. fallback artifact 사용 여부와 launch readiness도 함께 확인하세요."
        status={
          <>
            <strong>{text(state.runtime_active_model ?? state.geometry_artifact_model ?? state.geometry_mode, "unknown geometry")}</strong>
            <span>{validationResult.split("\n", 1)[0]}</span>
          </>
        }
        actions={
          <button
            className="action-button"
            disabled={busyAction !== null}
            onClick={() => void runAction("Validate runtime", () => validateRuntime())}
            type="button"
          >
            Validate runtime
          </button>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Validation mode" value={text(state.validation_mode, "read-only")} />
        <MetricCard label="Bakeoff winner" value={text(state.bakeoff_selected_model, "-")} detail="selection truth" tone="accent" />
        <MetricCard label="승격된 runtime" value={text(state.promoted_runtime_model, "-")} detail="promotion truth" />
        <MetricCard
          label="실제 active runtime"
          value={text(state.runtime_active_model ?? state.geometry_artifact_model ?? state.geometry_mode, "unknown")}
          detail={`runtime=${displayRuntimeStatus(state.status ?? "idle")}`}
          tone="accent"
        />
        <MetricCard
          label="Residual"
          value={text(state.geometry_residual_model, "-")}
          detail={text(state.geometry_rollout_status, "-")}
        />
        <MetricCard
          label="Launch ready"
          value={displayBooleanState(state.runtime_launch_ready ?? state.launch_ready ?? state.prepared)}
          detail={text(state.runtime_launch_ready_reason ?? state.launch_ready_reason)}
          tone={Boolean(state.runtime_launch_ready ?? state.launch_ready ?? state.prepared) ? "accent" : "warn"}
        />
        <MetricCard label="GPU path" value={text(state.gpu_path_mode, "unknown")} detail={displayBooleanState(state.gpu_path_ready)} />
      </div>

      <section className="panel">
        <div className="panel-title">Artifact truth</div>
        <div className="definition-list">
          <div className="definition-item">
            <span className="definition-label">Artifact path</span>
            <span className="definition-value">
              {text(state.runtime_active_artifact_path ?? state.geometry_artifact_path ?? preparedPlan?.geometry_artifact_path, "not prepared")}
            </span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Artifact checksum</span>
            <span className="definition-value">{text(state.geometry_artifact_checksum, "not computed")}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Fallback only</span>
            <span className="definition-value">{displayBooleanState(state.geometry_fallback_only)}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Promotion succeeded</span>
            <span className="definition-value">{displayBooleanState(state.promotion_succeeded)}</span>
          </div>
          <div className="definition-item">
            <span className="definition-label">Promotion blocker</span>
            <span className="definition-value">{text(state.promotion_blocker_reason, "-")}</span>
          </div>
        </div>
      </section>

      <details className="details-panel" open>
        <summary className="details-summary">Latest validation payload</summary>
        <pre className="action-output">{validationResult}</pre>
      </details>
    </section>
  );
}
