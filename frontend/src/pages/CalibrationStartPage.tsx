import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { CalibrationStepper } from "../components/CalibrationStepper";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { startCalibrationSession, useCurrentHomography, type CalibrationState } from "../lib/api";
import { displayBooleanState, displayCalibrationStep } from "../lib/display";
import { useCalibrationState } from "../lib/useCalibrationState";

export function CalibrationStartPage() {
  const navigate = useNavigate();
  const { state, loading, error, setState } = useCalibrationState();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("준비되었습니다.");
  const [outputStandard, setOutputStandard] = useState("");
  const [runCalibrationFirst, setRunCalibrationFirst] = useState(true);
  const [openVlcLowLatency, setOpenVlcLowLatency] = useState(false);

  useEffect(() => {
    if (!state) {
      return;
    }
    setOutputStandard((current) => current || state.start.output_standard || state.output_standard_options[0] || "");
    setRunCalibrationFirst(state.start.run_calibration_first);
    setOpenVlcLowLatency(state.start.open_vlc_low_latency);
  }, [state]);

  const runAction = async (label: string, action: () => Promise<CalibrationState>) => {
    setBusyAction(label);
    setActionStatus(`${label} 작업을 실행하는 중입니다...`);
    try {
      const nextState = await action();
      if (nextState) {
        setState(nextState);
        setActionStatus(`${label} 완료.`);
        navigate(nextState.route);
      }
    } catch (err) {
      setActionStatus(`${label} 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const homography = state?.start.homography ?? {};

  return (
    <section className="page">
      <CalibrationStepper />

      <PageHeader
        eyebrow="캘리브레이션"
        title="기하 준비 방식을 선택하세요"
        description="새 정렬이 필요하면 점 선택 기반 보정을 시작하고, 이미 실행 가능한 상태라면 현재 기하를 재사용하세요."
        status={
          <>
            <strong>{loading ? "캘리브레이션 상태를 불러오는 중입니다..." : error || actionStatus}</strong>
            <span>나중에 캘리브레이션을 승인하더라도 런타임이 자동으로 시작되지는 않습니다.</span>
          </>
        }
        actions={
          <>
            <label className="field-group field-group-compact">
              <span className="field-label">출력 표준</span>
              <select
                id="output-standard"
                className="field-input"
                value={outputStandard}
                onChange={(event) => setOutputStandard(event.target.value)}
              >
                {(state?.output_standard_options ?? []).map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="action-button"
              disabled={busyAction !== null || !outputStandard}
              onClick={() =>
                void runAction("보정 시작", () =>
                  startCalibrationSession({
                    output_standard: outputStandard,
                    run_calibration_first: runCalibrationFirst,
                    open_vlc_low_latency: openVlcLowLatency,
                  }),
                )
              }
              type="button"
            >
              점 선택 시작
            </button>
            <button
              className="action-button secondary"
              disabled={busyAction !== null || !state?.start.use_current_homography_enabled}
              onClick={() =>
                void runAction("현재 기하 사용", () =>
                  useCurrentHomography({
                    output_standard: outputStandard,
                    run_calibration_first: runCalibrationFirst,
                    open_vlc_low_latency: openVlcLowLatency,
                  }),
                )
              }
              type="button"
            >
              현재 기하 사용
            </button>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="현재 단계" value={displayCalibrationStep(state?.workflow.current_step ?? "start")} detail="통합 React 화면" tone="accent" />
        <MetricCard label="호모그래피 기준" value={String(homography.distortion_reference ?? "raw")} detail={`실행 가능=${displayBooleanState(homography.launch_ready ?? false)}`} />
        <MetricCard label="수동 점 수" value={String(homography.manual_points_count ?? 0)} detail={`inlier=${String(homography.inliers_count ?? 0)}`} />
        <MetricCard
          label="재투영 오차"
          value={Number(homography.mean_reprojection_error ?? 0).toFixed(3)}
          detail={`inlier 비율=${Number(homography.inlier_ratio ?? 0).toFixed(3)}`}
          tone="warn"
        />
      </div>
      <section className="panel">
        <div className="panel-title">다음 단계</div>
        <div className="action-output">
          <div>현재 단계: {displayCalibrationStep(state?.workflow.current_step ?? "start")}</div>
          <div>호모그래피 기준: {state?.workflow.homography_reference ?? "raw"}</div>
          <div>현재 기하 사용 가능: {state?.start.use_current_homography_enabled ? "사용 가능" : "사용 불가"}</div>
          <div>세션의 수동 점 쌍 수: {state?.workflow.manual_pair_count ?? 0}</div>
          <div>승인 후에는 운영 화면으로 이동해 `준비 → 시작` 순서로 실행합니다.</div>
        </div>
      </section>

      <details className="details-panel">
        <summary className="details-summary">고급 실행 옵션</summary>
        <div className="operator-actions operator-actions-inline">
          <label className="toggle-field">
            <input checked={runCalibrationFirst} onChange={(event) => setRunCalibrationFirst(event.target.checked)} type="checkbox" />
            <span>먼저 캘리브레이션 실행</span>
          </label>
          <label className="toggle-field">
            <input checked={openVlcLowLatency} onChange={(event) => setOpenVlcLowLatency(event.target.checked)} type="checkbox" />
            <span>저지연 VLC Transmit 열기</span>
          </label>
        </div>
      </details>
    </section>
  );
}
