import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { CalibrationStepper } from "../components/CalibrationStepper";
import { CalibrationImage } from "../components/CalibrationImage";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  acceptCalibrationReview,
  cancelCalibrationReview,
  fetchCalibrationReview,
  type CalibrationState,
} from "../lib/api";
import { displayCalibrationStep } from "../lib/display";
import { useCalibrationState } from "../lib/useCalibrationState";

export function CalibrationReviewPage() {
  const navigate = useNavigate();
  const { state, loading, error, setState } = useCalibrationState(fetchCalibrationReview);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("스티치 미리보기와 inlier 상태를 검토하세요.");

  const runAction = async (label: string, action: () => Promise<CalibrationState>) => {
    setBusyAction(label);
    setActionStatus(`${label} 작업을 실행하는 중입니다...`);
    try {
      const nextState = await action();
      setState(nextState);
      setActionStatus(`${label} 완료.`);
      navigate(nextState.route);
    } catch (err) {
      setActionStatus(`${label} 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const candidate = state?.review.candidate ?? {};

  return (
    <section className="page">
      <CalibrationStepper />

      <PageHeader
        eyebrow="캘리브레이션"
        title="후보 변환 검토"
        description="이 후보를 저장하기 전에 스티치 품질과 inlier 범위를 확인하세요. 승인하면 마지막 스티치 점검 단계로 바로 이동합니다."
        status={
          <>
            <strong>{loading ? "검토 화면을 불러오는 중입니다..." : error || actionStatus}</strong>
            <span>겹침이 어색하거나 inlier 상태가 약하면 뒤로 돌아가 점을 다시 선택하세요.</span>
          </>
        }
        actions={
          <>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("캘리브레이션으로 돌아가기", cancelCalibrationReview)} type="button">
              점 선택으로 돌아가기
            </button>
            <button className="action-button" disabled={busyAction !== null} onClick={() => void runAction("캘리브레이션 승인", acceptCalibrationReview)} type="button">
              저장 후 계속
            </button>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="수동 점 수" value={String(candidate.manual_points_count ?? 0)} detail={`inlier=${String(candidate.inliers_count ?? 0)}`} tone="accent" />
        <MetricCard label="Inlier 비율" value={Number(candidate.inlier_ratio ?? 0).toFixed(3)} detail={`기준=${String(candidate.homography_reference ?? "raw")}`} />
        <MetricCard label="재투영 오차" value={Number(candidate.mean_reprojection_error ?? 0).toFixed(3)} detail="낮을수록 좋습니다" tone="warn" />
        <MetricCard
          label="출력 크기"
          value={Array.isArray(candidate.output_resolution) ? candidate.output_resolution.join(" x ") : "-"}
          detail={displayCalibrationStep(state?.workflow.current_step ?? "calibration-review")}
        />
      </div>
      <div className="panel-grid calibration-grid">
        <section className="panel wide">
          <div className="panel-title">보정 스티치 미리보기</div>
          <CalibrationImage alt="보정 스티치 미리보기" src={state?.review.preview_image_url ?? ""} />
        </section>
        <section className="panel wide">
          <div className="panel-title">Inlier 매치</div>
          <CalibrationImage alt="보정 inlier 매치" src={state?.review.inlier_image_url ?? ""} />
        </section>
      </div>
    </section>
  );
}
