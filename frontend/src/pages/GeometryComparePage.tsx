import { useEffect, useState } from "react";

import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  apiUrl,
  fetchGeometryBakeoffState,
  runGeometryBakeoff,
  useGeometryBakeoffWinner,
  type GeometryBakeoffCandidate,
  type GeometryBakeoffState,
} from "../lib/api";
import { displayGeometryMode } from "../lib/display";
import { useRuntimeFeed } from "../lib/useRuntimeFeed";

function metric(value: number, digits = 3): string {
  if (!Number.isFinite(value)) {
    return "-";
  }
  return Number(value).toFixed(digits);
}

function candidateAsset(url?: string): string {
  return url ? apiUrl(url) : "";
}

function text(value: unknown, fallback = "-"): string {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

function emptyState(): GeometryBakeoffState {
  return {
    status: "idle",
    session_id: "",
    bundle_dir: "",
    selected_candidate_model: "",
    promoted_candidate_model: "",
    runtime_active_artifact_path: "",
    promotion_attempted: false,
    promotion_succeeded: false,
    promotion_blocker_reason: "",
    candidates: [],
  };
}

function CandidateCard({
  candidate,
  busy,
  onUseWinner,
}: {
  candidate: GeometryBakeoffCandidate;
  busy: boolean;
  onUseWinner: (model: string) => void;
}) {
  const runtimeReady = Boolean(candidate.runtime_launch_ready);
  const rolloutStatus = text(candidate.geometry_rollout_status, "unknown");
  const runtimeReason = text(candidate.runtime_launch_ready_reason, "");

  return (
    <section className="panel wide">
      <div className="panel-title">{displayGeometryMode(candidate.model)}</div>
      <div className="panel-subtitle">
        global={candidate.global_model} / residual={candidate.residual_model} / projection={candidate.projection_model}
      </div>

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="재투영 오차" value={metric(candidate.mean_reprojection_error_px)} detail="mean px" tone="accent" />
        <MetricCard label="Vertical p90" value={metric(candidate.vertical_misalignment_p90_px)} detail="px" />
        <MetricCard label="오른쪽 drift" value={metric(candidate.right_edge_scale_drift)} detail="1.0에 가까울수록 좋음" />
        <MetricCard
          label="Seam visibility"
          value={metric(candidate.seam_visibility_score)}
          detail={candidate.status}
          tone={candidate.fallback_used ? "warn" : "calm"}
        />
      </div>

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Overlap luma" value={metric(candidate.overlap_luma_diff)} detail="낮을수록 좋음" />
        <MetricCard label="Crop ratio" value={metric(candidate.crop_ratio)} detail="cropped/full" />
        <MetricCard label="Mesh disp" value={metric(candidate.mesh_max_displacement_px)} detail="px" />
        <MetricCard
          label="Mesh scale drift"
          value={metric(candidate.mesh_max_local_scale_drift)}
          detail={candidate.fallback_used ? "degraded" : "mesh active"}
          tone={candidate.fallback_used ? "warn" : "calm"}
        />
      </div>

      <div className="panel-grid calibration-grid">
        <section className="panel">
          <div className="panel-title">1분 비교 영상</div>
          {candidate.stitched_video_url ? (
            <video
              className="calibration-image"
              controls
              muted
              loop
              playsInline
              preload="metadata"
              src={candidateAsset(candidate.stitched_video_url)}
            />
          ) : (
            <div className="muted">아직 비교 영상이 없습니다.</div>
          )}
          <div className="muted" style={{ marginTop: "8px" }}>
            {candidate.video_duration_sec
              ? `${metric(candidate.video_duration_sec, 1)}s / ${metric(candidate.video_fps ?? 0, 0)} fps`
              : "video unavailable"}
          </div>
        </section>
        <section className="panel">
          <div className="panel-title">Stitched preview</div>
          {candidate.stitched_preview_url ? (
            <img
              className="calibration-image"
              alt={`${candidate.model} stitched preview`}
              src={candidateAsset(candidate.stitched_preview_url)}
            />
          ) : (
            <div className="muted">No preview</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">Overlap crop</div>
          {candidate.overlap_crop_url ? (
            <img
              className="calibration-image"
              alt={`${candidate.model} overlap crop`}
              src={candidateAsset(candidate.overlap_crop_url)}
            />
          ) : (
            <div className="muted">No overlap crop</div>
          )}
        </section>
      </div>

      <div className="panel-grid">
        <section className="panel">
          <div className="panel-title">Seam debug</div>
          {candidate.seam_debug_url ? (
            <img
              className="calibration-image"
              alt={`${candidate.model} seam debug`}
              src={candidateAsset(candidate.seam_debug_url)}
            />
          ) : (
            <div className="muted">No seam debug</div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title">후보 상태</div>
          <div className="action-output">
            <div>선택됨: {candidate.selected ? "예" : "아니오"}</div>
            <div>런타임 승격 가능: {runtimeReady ? "예" : "아직 아님"}</div>
            <div>Rollout status: {rolloutStatus}</div>
            <div>Runtime artifact: {candidate.runtime_artifact_path || "not launch-ready yet"}</div>
            <div>Exposure: {candidate.exposure_model}</div>
            <div>Blend: {candidate.blend_model}</div>
            <div>Crop: {candidate.crop_model}</div>
          </div>
          <div className="operator-actions operator-actions-inline">
            <button className="action-button" disabled={busy} onClick={() => onUseWinner(candidate.model)} type="button">
              이 후보로 사용
            </button>
          </div>
          {!runtimeReady ? (
            <div className="muted" style={{ marginTop: "12px" }}>
              {runtimeReason || "지금은 bakeoff winner로만 선택되고, runtime 승격은 blocker reason과 함께 실패합니다."}
            </div>
          ) : null}
        </section>
      </div>
    </section>
  );
}

export function GeometryComparePage() {
  const [state, setState] = useState<GeometryBakeoffState>(emptyState());
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionSummary, setActionSummary] = useState(
    "Bakeoff를 실행해 4개 후보를 만든 뒤 winner를 고르세요. 선택된 winner가 runtime으로 바로 올라가지 않으면 blocker reason을 함께 보여줍니다.",
  );
  const { state: runtimeState } = useRuntimeFeed();

  const refresh = async () => {
    setState(await fetchGeometryBakeoffState());
  };

  useEffect(() => {
    void refresh();
  }, []);

  const runAction = async (label: string, action: () => Promise<GeometryBakeoffState>) => {
    setBusyAction(label);
    setActionSummary(`${label} 진행 중...`);
    try {
      const nextState = await action();
      setState(nextState);
      setActionSummary(`${label} 완료`);
    } catch (error) {
      setActionSummary(`${label} 실패: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <section className="page">
      <PageHeader
        eyebrow="Bakeoff"
        title="오프라인 Geometry Bakeoff"
        description="manual point 없이 auto-only로 4개 후보를 같은 clip에서 비교합니다. 이 화면은 품질 비교와 winner 선택만 담당하고, 실제 송출은 Run 화면에서 진행합니다."
        status={
          <>
            <strong>{state.session_id ? `Session ${state.session_id}` : "아직 bakeoff bundle이 없습니다."}</strong>
            <span>{actionSummary}</span>
          </>
        }
        actions={
          <>
            <button
              className="action-button"
              disabled={busyAction !== null}
              onClick={() => void runAction("Bakeoff 실행", () => runGeometryBakeoff({ video_duration_sec: 60, video_fps: 15 }))}
              type="button"
            >
              1분 bakeoff 실행
            </button>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void refresh()} type="button">
              새로고침
            </button>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="Bundle" value={state.session_id || "-"} detail={state.bundle_dir || "no bundle"} tone="accent" />
        <MetricCard label="선택된 winner" value={state.selected_candidate_model || "-"} detail="bakeoff truth" />
        <MetricCard
          label="승격된 runtime"
          value={state.promoted_candidate_model || "-"}
          detail={state.runtime_active_artifact_path || "runtime unchanged"}
          tone={state.promoted_candidate_model ? "accent" : "calm"}
        />
        <MetricCard
          label="실제 active runtime"
          value={displayGeometryMode(runtimeState.runtime_active_model ?? runtimeState.geometry_artifact_model ?? runtimeState.geometry_mode ?? "-")}
          detail={text(runtimeState.runtime_active_artifact_path ?? runtimeState.geometry_artifact_path, "not prepared")}
          tone="accent"
        />
        <MetricCard label="후보 개수" value={String(state.candidates.length)} detail="예상 4개" />
      </div>

      {state.promotion_attempted && !state.promotion_succeeded ? (
        <section className="status-strip warn">
          <div className="status-strip-title">승격 blocker</div>
          <div className="status-strip-body">
            winner는 선택됐지만 runtime 승격은 막혀 있습니다. bakeoff 선택 결과와 실제 active runtime model은 분리해서 확인하세요.
          </div>
          <div className="status-strip-footnote">{state.promotion_blocker_reason || "unknown blocker"}</div>
        </section>
      ) : null}

      <section className="status-strip">
        <div className="status-strip-title">Bakeoff policy</div>
        <div className="status-strip-body">
          front-end는 SIFT primary, ORB fallback, BFMatcher + ratio test, RANSAC homography로 고정합니다. 4개 후보는 모두 같은
          exposure / seam / blend / crop 정책을 쓰고, 각 후보마다 1분 stitched comparison video를 저장합니다.
        </div>
      </section>

      {state.candidates.length === 0 ? (
        <section className="panel">
          <div className="panel-title">아직 bakeoff bundle이 없습니다</div>
          <p className="muted">
            먼저 <strong>1분 bakeoff 실행</strong>을 눌러 같은 clip을 캡처하고, 후보별 stitched video를 만들어 비교하세요.
          </p>
        </section>
      ) : (
        <div className="panel-grid compare-board">
          {state.candidates.map((candidate) => (
            <CandidateCard
              key={candidate.model}
              candidate={candidate}
              busy={busyAction !== null}
              onUseWinner={(model) =>
                void runAction("이 후보로 사용", () => useGeometryBakeoffWinner({ bundle_dir: state.bundle_dir, model }))
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}
