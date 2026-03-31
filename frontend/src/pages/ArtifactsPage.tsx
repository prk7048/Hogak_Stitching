import { useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { displayGeometryMode } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

export function ArtifactsPage() {
  const { artifacts, refreshRuntime } = useRuntimeFeed();
  const [refreshMessage, setRefreshMessage] = useState("아티팩트 목록은 백엔드에서 몇 초마다 다시 불러옵니다.");

  const refreshArtifacts = async () => {
    setRefreshMessage("아티팩트 목록을 새로고치는 중입니다...");
    await refreshRuntime();
    setRefreshMessage("아티팩트 목록을 새로고쳤습니다.");
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="점검 / 아티팩트"
        title="저장된 기하 아티팩트 확인"
        description="아티팩트 메타데이터를 비교하거나, 준비/검증에 사용할 기하가 무엇인지 확인할 때 사용하는 화면입니다."
        status={<span>{refreshMessage}</span>}
        actions={
          <button className="action-button" onClick={() => void refreshArtifacts()} type="button">
            아티팩트 새로고침
          </button>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="아티팩트 수" value={String(artifacts.length)} tone="accent" />
        <MetricCard
          label="대표 모델"
          value={displayGeometryMode(artifacts[0]?.model ?? artifacts[0]?.geometry_model ?? "none")}
          detail={artifacts[0]?.name ?? "불러온 아티팩트가 없습니다"}
        />
      </div>

      <section className="panel">
        <div className="panel-title">상태</div>
        <div className="action-output">{refreshMessage}</div>
      </section>
      <section className="panel">
        <div className="panel-title">기하 아티팩트</div>
        <div className="artifact-list">
          {artifacts.length === 0 ? <div className="muted">아직 아티팩트를 찾지 못했습니다.</div> : null}
          {artifacts.map((artifact) => (
            <article className="artifact-item" key={artifact.name}>
              <div className="artifact-name">{artifact.name}</div>
              <div className="definition-list">
                <div className="definition-item">
                  <span className="definition-label">모델</span>
                  <span className="definition-value">{displayGeometryMode(artifact.model ?? artifact.geometry_model ?? "unknown")}</span>
                </div>
                <div className="definition-item">
                  <span className="definition-label">스키마</span>
                  <span className="definition-value">{String(artifact.schema_version ?? "n/a")}</span>
                </div>
                <div className="definition-item">
                  <span className="definition-label">출력 크기</span>
                  <span className="definition-value">
                    {Array.isArray(artifact.output_resolution) && artifact.output_resolution.length > 0
                      ? artifact.output_resolution.join(" x ")
                      : "n/a"}
                  </span>
                </div>
                <div className="definition-item">
                  <span className="definition-label">경로</span>
                  <span className="definition-value">{artifact.path ?? "n/a"}</span>
                </div>
              </div>
              <details className="artifact-details">
                <summary className="details-summary">원본 메타데이터</summary>
                <pre>{JSON.stringify(artifact, null, 2)}</pre>
              </details>
            </article>
          ))}
        </div>
      </section>
    </section>
  );
}
