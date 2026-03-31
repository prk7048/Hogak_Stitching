import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { describeRuntimeActionResult, validateRuntime } from "../lib/api";
import { displayBooleanState, displayRuntimeStatus } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function ValidationPage() {
  const { state, refreshRuntime } = useRuntimeFeed();
  const preparedPlan = state.prepared_plan as Record<string, unknown> | undefined;
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState("아직 검증을 실행하지 않았습니다.");

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setValidationResult(`${label} 작업을 실행하는 중입니다...`);
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
        eyebrow="점검 / 검증"
        title="읽기 전용 검증 실행"
        description="런타임 상태를 바꾸기 전에 실행 조건과 아티팩트 일관성을 확인합니다. 이 페이지는 항상 읽기 전용이어야 합니다."
        status={
          <>
            <strong>검증은 런타임을 시작하지 않습니다.</strong>
            <span>{validationResult.startsWith("아직 검증") ? "아직 검증을 실행하지 않았습니다." : validationResult.split("\n", 1)[0]}</span>
          </>
        }
        actions={
          <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("검증", () => validateRuntime())} type="button">
            런타임 검증
          </button>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="검증 모드" value={state.validation_mode === "read-only" ? "읽기 전용" : String(state.validation_mode ?? "읽기 전용")} />
        <MetricCard label="아티팩트 체크섬" value={String(state.geometry_artifact_checksum ?? preparedPlan?.geometry_artifact_path ?? "대기 중")} tone="accent" />
        <MetricCard label="실행 가능 여부" value={displayBooleanState(state.launch_ready ?? state.prepared)} tone="warn" />
        <MetricCard label="엄격한 fresh 적용" value={displayBooleanState(state.strict_fresh ?? state.running)} detail={`현재 런타임 상태=${displayRuntimeStatus(state.status ?? "idle")}`} />
      </div>
      <section className="panel">
        <div className="panel-title">확인 항목</div>
        <ul className="check-list">
          <li>schema v2 envelope 수용 여부</li>
          <li>알 수 없는 필드 거부 여부</li>
          <li>검증 전후 기하 아티팩트가 바뀌지 않는지</li>
          <li>런타임 생명주기가 대기 상태를 유지하는지</li>
        </ul>
      </section>
      <details className="details-panel" open>
        <summary className="details-summary">최근 검증 결과</summary>
        <pre className="action-output">{validationResult}</pre>
      </details>
    </section>
  );
}
