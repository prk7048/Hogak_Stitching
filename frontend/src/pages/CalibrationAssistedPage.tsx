import { useState, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";

import { CalibrationStepper } from "../components/CalibrationStepper";
import { CalibrationImage } from "../components/CalibrationImage";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import {
  addCalibrationPair,
  clearCalibrationPairs,
  computeCalibrationCandidate,
  deleteCalibrationPair,
  refreshCalibrationFrames,
  selectCalibrationPair,
  undoCalibrationPair,
  type CalibrationState,
} from "../lib/api";
import { useCalibrationState } from "../lib/useCalibrationState";

function clickToPreviewCoordinates(event: MouseEvent<HTMLImageElement>): { x: number; y: number } {
  const image = event.currentTarget;
  const bounds = image.getBoundingClientRect();
  const naturalWidth = image.naturalWidth || image.width || 1;
  const naturalHeight = image.naturalHeight || image.height || 1;
  const x = ((event.clientX - bounds.left) / Math.max(1, bounds.width)) * naturalWidth;
  const y = ((event.clientY - bounds.top) / Math.max(1, bounds.height)) * naturalHeight;
  return { x, y };
}

export function CalibrationAssistedPage() {
  const navigate = useNavigate();
  const { state, loading, error, setState } = useCalibrationState();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("왼쪽 점을 먼저 찍고, 그다음 오른쪽 점을 찍으세요.");

  const runAction = async (label: string, action: () => Promise<CalibrationState>) => {
    setBusyAction(label);
    setActionStatus(`${label} 작업을 실행하는 중입니다...`);
    try {
      const nextState = await action();
      setState(nextState);
      setActionStatus(`${label} 완료.`);
      if (nextState.route !== "/calibration/assisted") {
        navigate(nextState.route);
      }
    } catch (err) {
      setActionStatus(`${label} 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const handleClick = (slot: "left" | "right") => async (event: MouseEvent<HTMLImageElement>) => {
    const { x, y } = clickToPreviewCoordinates(event);
    await runAction(`${slot === "left" ? "왼쪽" : "오른쪽"} 점 추가`, () => addCalibrationPair({ slot, x, y }));
  };

  return (
    <section className="page">
      <CalibrationStepper />

      <PageHeader
        eyebrow="캘리브레이션"
        title="대응점 선택"
        description="왼쪽 이미지를 먼저 클릭한 뒤, 오른쪽 이미지에서 대응되는 위치를 고르세요. 후보를 계산하기 전에 최소 4쌍 이상을 만드세요."
        status={
          <>
            <strong>{loading ? "캘리브레이션 상태를 불러오는 중입니다..." : error || actionStatus}</strong>
            <span>권장: 겹침 영역 전반에 걸쳐 6~10쌍 정도를 고르게 선택하세요.</span>
          </>
        }
        actions={
          <>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("프레임 새로고침", refreshCalibrationFrames)} type="button">
              프레임 새로고침
            </button>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("점 쌍 되돌리기", undoCalibrationPair)} type="button">
              되돌리기
            </button>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("선택한 점 쌍 삭제", deleteCalibrationPair)} type="button">
              선택 삭제
            </button>
            <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("점 쌍 전체 삭제", clearCalibrationPairs)} type="button">
              전체 삭제
            </button>
            <button
              className="action-button"
              disabled={busyAction !== null || !state?.assisted.compute_enabled}
              onClick={() => void runAction("보정 후보 계산", computeCalibrationCandidate)}
              type="button"
            >
              후보 계산
            </button>
          </>
        }
      />

      <div className="metric-grid metric-grid-compact">
        <MetricCard label="점 쌍 수" value={String(state?.assisted.pair_count ?? 0)} detail="최소 4쌍, 권장 6~10쌍" tone="accent" />
        <MetricCard label="다음 선택 위치" value={String(state?.assisted.pending_side === "right" ? "오른쪽" : "왼쪽")} detail="왼쪽 클릭 → 오른쪽 클릭" />
        <MetricCard
          label="선택된 점 쌍"
          value={state?.assisted.selected_pair_index === null || state?.assisted.selected_pair_index === undefined ? "없음" : String((state.assisted.selected_pair_index ?? 0) + 1)}
          detail={`수동 점 쌍=${String(state?.workflow.manual_pair_count ?? 0)}`}
        />
        <MetricCard
          label="현재 단계"
          value={String(state?.workflow.current_step ?? "start")}
          detail={state?.assisted.compute_enabled ? "계산 준비 완료" : "점 쌍을 더 추가하세요"}
          tone={state?.assisted.compute_enabled ? "accent" : "warn"}
        />
      </div>
      <div className="panel-grid calibration-grid">
        <section className="panel wide">
          <div className="panel-title">왼쪽 프레임</div>
          <div className="panel-subtitle">다음 왼쪽 점을 여기에서 고르세요.</div>
          <CalibrationImage alt="왼쪽 보정 프레임" clickable src={state?.assisted.left_image_url ?? ""} onClick={(event) => void handleClick("left")(event)} />
        </section>
        <section className="panel wide">
          <div className="panel-title">오른쪽 프레임</div>
          <div className="panel-subtitle">왼쪽을 고른 뒤, 대응되는 오른쪽 점을 클릭하세요.</div>
          <CalibrationImage alt="오른쪽 보정 프레임" clickable src={state?.assisted.right_image_url ?? ""} onClick={(event) => void handleClick("right")(event)} />
        </section>
      </div>
      <div className="panel-grid calibration-grid">
        <section className="panel">
          <div className="panel-title">점 쌍 목록</div>
          <div className="field-group">
            <label className="field-label" htmlFor="pair-select">
              선택된 점 쌍
            </label>
            <select
              id="pair-select"
              className="field-input"
              value={state?.assisted.selected_pair_index ?? ""}
              onChange={(event) => {
                if (!event.target.value) {
                  return;
                }
                void runAction("점 쌍 선택", () => selectCalibrationPair(Number(event.target.value)));
              }}
            >
              <option value="">점 쌍 선택</option>
              {(state?.assisted.pairs ?? []).map((pair) => (
                <option key={pair.index} value={pair.index}>
                  {pair.label}
                </option>
              ))}
            </select>
          </div>
          <div className="pair-list">
            {(state?.assisted.pairs ?? []).length === 0 ? <div className="muted">아직 수동 점 쌍이 없습니다.</div> : null}
            {(state?.assisted.pairs ?? []).map((pair) => (
              <button
                key={pair.index}
                className={`pair-chip${pair.selected ? " active" : ""}`}
                onClick={() => void runAction("점 쌍 선택", () => selectCalibrationPair(pair.index))}
                type="button"
              >
                {pair.label}
              </button>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-title">빠른 안내</div>
          <div className="action-output">
            <div>1. 왼쪽 이미지에서 한 점을 선택합니다.</div>
            <div>2. 오른쪽 이미지에서 대응되는 점을 선택합니다.</div>
            <div>3. 최소 4쌍이 될 때까지 반복합니다.</div>
            <div>4. 후보 계산을 눌러 검토 단계로 이동합니다.</div>
            <div>
              대기 중인 왼쪽 점:{" "}
              {state?.assisted.pending_left_point ? `${Math.round(state.assisted.pending_left_point[0])}, ${Math.round(state.assisted.pending_left_point[1])}` : "없음"}
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}
