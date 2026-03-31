import { useState, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";

import { CalibrationImage } from "../components/CalibrationImage";
import { MetricCard } from "../components/MetricCard";
import {
  addCalibrationPair,
  computeCalibrationCandidate,
  deleteCalibrationPair,
  refreshCalibrationFrames,
  selectCalibrationPair,
  undoCalibrationPair,
  clearCalibrationPairs,
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
  const [actionStatus, setActionStatus] = useState("Click left point, then right point.");

  const runAction = async (label: string, action: () => Promise<CalibrationState>) => {
    setBusyAction(label);
    setActionStatus(`${label} running...`);
    try {
      const nextState = await action();
      setState(nextState);
      setActionStatus(`${label} complete.`);
      if (nextState.route !== "/calibration/assisted") {
        navigate(nextState.route);
      }
    } catch (err) {
      setActionStatus(`${label} failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const handleClick = (slot: "left" | "right") => async (event: MouseEvent<HTMLImageElement>) => {
    const { x, y } = clickToPreviewCoordinates(event);
    await runAction(`${slot} point`, () => addCalibrationPair({ slot, x, y }));
  };

  return (
    <section className="page">
      <div className="hero">
        <div>
          <div className="eyebrow">Calibration</div>
          <h2>Assisted correspondence picking</h2>
          <p>Use left click then right click to build each correspondence pair. Refresh frames if the representative images need updating.</p>
          <div className="action-status">{loading ? "Loading calibration state..." : error || actionStatus}</div>
        </div>
        <div className="operator-actions">
          <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("Refresh frames", refreshCalibrationFrames)} type="button">
            Refresh frames
          </button>
          <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("Undo pair", undoCalibrationPair)} type="button">
            Undo
          </button>
          <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("Delete pair", deleteCalibrationPair)} type="button">
            Delete selected
          </button>
          <button className="action-button secondary" disabled={busyAction !== null} onClick={() => void runAction("Clear pairs", clearCalibrationPairs)} type="button">
            Clear all
          </button>
          <button
            className="action-button"
            disabled={busyAction !== null || !state?.assisted.compute_enabled}
            onClick={() => void runAction("Compute calibration", computeCalibrationCandidate)}
            type="button"
          >
            Compute calibration
          </button>
        </div>
      </div>
      <div className="metric-grid">
        <MetricCard label="Pair count" value={String(state?.assisted.pair_count ?? 0)} detail="min 4, recommended 6-10" tone="accent" />
        <MetricCard label="Pending side" value={String(state?.assisted.pending_side ?? "left")} detail="left click -> right click" />
        <MetricCard
          label="Selected pair"
          value={state?.assisted.selected_pair_index === null || state?.assisted.selected_pair_index === undefined ? "none" : String((state.assisted.selected_pair_index ?? 0) + 1)}
          detail={`manual pairs=${String(state?.workflow.manual_pair_count ?? 0)}`}
        />
        <MetricCard
          label="Current step"
          value={String(state?.workflow.current_step ?? "start")}
          detail={state?.assisted.compute_enabled ? "ready to compute" : "add more pairs"}
          tone={state?.assisted.compute_enabled ? "accent" : "warn"}
        />
      </div>
      <div className="panel-grid calibration-grid">
        <section className="panel wide">
          <div className="panel-title">Left frame</div>
          <CalibrationImage alt="left calibration frame" clickable src={state?.assisted.left_image_url ?? ""} onClick={(event) => void handleClick("left")(event)} />
        </section>
        <section className="panel wide">
          <div className="panel-title">Right frame</div>
          <CalibrationImage alt="right calibration frame" clickable src={state?.assisted.right_image_url ?? ""} onClick={(event) => void handleClick("right")(event)} />
        </section>
      </div>
      <div className="panel-grid calibration-grid">
        <section className="panel">
          <div className="panel-title">Pair list</div>
          <div className="field-group">
            <label className="field-label" htmlFor="pair-select">
              Selected pair
            </label>
            <select
              id="pair-select"
              className="field-input"
              value={state?.assisted.selected_pair_index ?? ""}
              onChange={(event) => {
                if (!event.target.value) {
                  return;
                }
                void runAction("Select pair", () => selectCalibrationPair(Number(event.target.value)));
              }}
            >
              <option value="">Select a pair</option>
              {(state?.assisted.pairs ?? []).map((pair) => (
                <option key={pair.index} value={pair.index}>
                  {pair.label}
                </option>
              ))}
            </select>
          </div>
          <div className="pair-list">
            {(state?.assisted.pairs ?? []).length === 0 ? <div className="muted">No manual pairs yet.</div> : null}
            {(state?.assisted.pairs ?? []).map((pair) => (
              <button
                key={pair.index}
                className={`pair-chip${pair.selected ? " active" : ""}`}
                onClick={() => void runAction("Select pair", () => selectCalibrationPair(pair.index))}
                type="button"
              >
                {pair.label}
              </button>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-title">Instructions</div>
          <div className="action-output">
            <div>1. Click a point on the left image.</div>
            <div>2. Click the matching point on the right image.</div>
            <div>3. Repeat until you have at least 4 pairs.</div>
            <div>4. Compute calibration to move to review.</div>
            <div>
              Pending left point:{" "}
              {state?.assisted.pending_left_point ? `${Math.round(state.assisted.pending_left_point[0])}, ${Math.round(state.assisted.pending_left_point[1])}` : "none"}
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}
