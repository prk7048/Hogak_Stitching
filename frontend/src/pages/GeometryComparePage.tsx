import { useEffect, useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { describeRuntimeActionResult, prepareRuntime, validateRuntime } from "../lib/api";
import { displayExposureMode, displayGeometryMode, displaySeamMode } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function GeometryComparePage() {
  const { state, artifacts, refreshRuntime } = useRuntimeFeed();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState("");
  const [actionSummary, setActionSummary] = useState("준비하거나 검증할 후보 아티팩트를 선택하세요.");

  useEffect(() => {
    if (!selectedArtifactPath && artifacts.length > 0) {
      setSelectedArtifactPath(artifacts[0]?.path || artifacts[0]?.name || "");
    }
  }, [artifacts, selectedArtifactPath]);

  const selectedArtifact = artifacts.find((artifact) => artifact.path === selectedArtifactPath || artifact.name === selectedArtifactPath) ?? artifacts[0];

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusyAction(label);
    setActionSummary(`${label} 작업을 실행하는 중입니다...`);
    try {
      const result = await action();
      setActionSummary(`${label}: ${describeRuntimeActionResult(result)}`);
      await refreshRuntime();
    } catch (error) {
      setActionSummary(`${label} 실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="점검 / 기하 비교"
        title="기본 경로와 후보 기하 비교"
        description="일상 운영보다는 롤아웃과 조사에 쓰는 화면입니다. 원통형 아티팩트를 측정하고 튜닝하는 동안 기본 경로를 함께 비교합니다."
        status={
          <>
            <strong>{selectedArtifact?.name ?? "선택된 기하 아티팩트가 없습니다."}</strong>
            <span>{actionSummary}</span>
          </>
        }
        actions={
          <>
            <label className="field-group field-group-compact">
              <span className="field-label">아티팩트</span>
              <select
                className="field-input"
                value={selectedArtifactPath}
                onChange={(event) => setSelectedArtifactPath(event.target.value)}
              >
                {artifacts.length === 0 ? <option value="">사용 가능한 아티팩트가 없습니다</option> : null}
                {artifacts.map((artifact) => (
                  <option key={artifact.path || artifact.name} value={artifact.path || artifact.name}>
                    {artifact.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="action-button"
              disabled={busyAction !== null || !selectedArtifact}
              onClick={() =>
                void runAction("준비", () =>
                  prepareRuntime({
                    geometry: {
                      artifact_path: selectedArtifact?.path || selectedArtifact?.name || "",
                    },
                  }),
                )
              }
              type="button"
            >
              선택한 기하 준비
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null || !selectedArtifact}
              onClick={() =>
                void runAction("검증", () =>
                  validateRuntime({
                    geometry: {
                      artifact_path: selectedArtifact?.path || selectedArtifact?.name || "",
                    },
                  }),
                )
              }
              type="button"
            >
              선택한 기하 검증
            </button>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="현재 기하 모드" value={displayGeometryMode(state.geometry_mode ?? "planar-homography")} tone="accent" />
        <MetricCard label="경계선 처리 모드" value={displaySeamMode(state.seam_mode ?? "feather")} />
        <MetricCard label="노출 보정 모드" value={displayExposureMode(state.exposure_mode ?? "none")} />
        <MetricCard label="선택 아티팩트" value={selectedArtifact?.name ?? "없음"} detail={String(selectedArtifact?.model ?? selectedArtifact?.geometry_model ?? "아티팩트 없음")} />
      </div>
      <div className="panel-grid">
        <section className="panel">
          <div className="panel-title">기본 평면 경로 (Planar)</div>
          <p className="muted">원통형 경로가 승인되기 전까지는 fallback 경로를 유지합니다.</p>
        </section>
        <section className="panel">
          <div className="panel-title">원통형 후보 (Cylindrical)</div>
          <p className="muted">기본 Transmit 경로를 바꾸기 전에 수직 정렬 오차, residual affine 오차, seam jitter를 측정합니다.</p>
        </section>
      </div>
      <details className="details-panel">
        <summary className="details-summary">작업 요약</summary>
        <pre className="action-output">{actionSummary}</pre>
      </details>
    </section>
  );
}
