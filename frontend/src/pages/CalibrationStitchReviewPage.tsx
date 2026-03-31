import { Link } from "react-router-dom";

import { CalibrationStepper } from "../components/CalibrationStepper";
import { CalibrationImage } from "../components/CalibrationImage";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { displayCalibrationStep } from "../lib/display";
import { fetchStitchReview } from "../lib/api";
import { useCalibrationState } from "../lib/useCalibrationState";

export function CalibrationStitchReviewPage() {
  const { state, loading, error } = useCalibrationState(fetchStitchReview);

  return (
    <section className="page">
      <CalibrationStepper />

      <PageHeader
        eyebrow="캘리브레이션"
        title="최종 오프라인 스티치 점검"
        description="이 미리보기는 저장된 geometry artifact만 사용합니다. 운영 화면으로 넘어가기 전 마지막 점검 단계입니다."
        status={
          <>
            <strong>{loading ? "스티치 점검 화면을 불러오는 중입니다..." : error || "정상으로 보이면 바로 운영 화면으로 이동하세요."}</strong>
            <span>이 페이지는 자체적으로 런타임을 시작하거나 UDP를 송출하지 않습니다.</span>
          </>
        }
        actions={
          <>
            <Link className="action-button secondary" to="/calibration/start">
              처음으로 돌아가기
            </Link>
            <Link className="action-button" to="/dashboard">
              운영 화면으로 이동
            </Link>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="현재 단계" value={displayCalibrationStep(state?.workflow.current_step ?? "stitch-review")} detail="오프라인 점검 전용" tone="accent" />
        <MetricCard label="Probe 수신" value={String(state?.stitch_review.probe_receive_uri ?? "-")} detail={String(state?.stitch_review.probe_sender_target ?? "-")} />
        <MetricCard
          label="Transmit 수신"
          value={String(state?.stitch_review.transmit_receive_uri ?? "-")}
          detail={String(state?.stitch_review.transmit_sender_target ?? "-")}
        />
        <MetricCard
          label="런타임 시작 위치"
          value="대시보드"
          detail="준비 → 시작"
          tone={state?.stitch_review.transmit_receive_uri ? "accent" : "warn"}
        />
      </div>
      <div className="panel-grid">
        <section className="panel wide">
          <div className="panel-title">스티치 점검 미리보기</div>
          <div className="panel-subtitle">저장된 geometry만 사용한 결과입니다. 아직 live transmit stream은 아닙니다.</div>
          <CalibrationImage alt="스티치 점검 미리보기" src={state?.stitch_review.preview_image_url ?? ""} />
        </section>
        <section className="panel">
          <div className="panel-title">다음 단계</div>
          <div className="action-output">
            <div>1. 운영 화면으로 이동합니다.</div>
            <div>2. `준비 → 시작` 순서로 실행합니다.</div>
            <div>3. 플레이어에서 메인 출력 수신 URI를 엽니다.</div>
            <div>미리보기 수신 URI: {state?.stitch_review.probe_receive_uri || "-"}</div>
            <div>메인 출력 수신 URI: {state?.stitch_review.transmit_receive_uri || "-"}</div>
            <div>같은 PC에서는 송출 대상 주소가 아니라 수신 URI를 VLC 또는 ffplay에 넣어야 합니다.</div>
            <div>
              미리보기 접근성: {state?.stitch_review.probe_loopback_only ? "같은 Windows PC에서만 열 수 있습니다." : "네트워크로 접근 가능한 대상이 필요합니다."}
            </div>
            <div>
              메인 출력 접근성: {state?.stitch_review.transmit_loopback_only ? "같은 Windows PC에서만 열 수 있습니다." : "네트워크로 접근 가능한 대상이 필요합니다."}
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}
