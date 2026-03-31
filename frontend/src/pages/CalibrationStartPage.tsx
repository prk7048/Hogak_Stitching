import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { MetricCard } from "../components/MetricCard";
import { startCalibrationSession, useCurrentHomography, type CalibrationState } from "../lib/api";
import { useCalibrationState } from "../lib/useCalibrationState";

export function CalibrationStartPage() {
  const navigate = useNavigate();
  const { state, loading, error, setState } = useCalibrationState();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState("Ready.");
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
    setActionStatus(`${label} running...`);
    try {
      const nextState = await action();
      if (nextState) {
        setState(nextState);
        setActionStatus(`${label} complete.`);
        navigate(nextState.route);
      }
    } catch (err) {
      setActionStatus(`${label} failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const homography = state?.start.homography ?? {};

  return (
    <section className="page">
      <div className="hero">
        <div>
          <div className="eyebrow">Calibration</div>
          <h2>Start the calibration workflow</h2>
          <p>Calibration, review, and runtime operation now live in the same React operator surface.</p>
          <div className="action-status">{loading ? "Loading calibration state..." : error || actionStatus}</div>
        </div>
        <div className="operator-actions">
          <div className="field-group">
            <label className="field-label" htmlFor="output-standard">
              Output standard
            </label>
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
          </div>
          <label className="toggle-field">
            <input checked={runCalibrationFirst} onChange={(event) => setRunCalibrationFirst(event.target.checked)} type="checkbox" />
            <span>Run calibration first</span>
          </label>
          <label className="toggle-field">
            <input checked={openVlcLowLatency} onChange={(event) => setOpenVlcLowLatency(event.target.checked)} type="checkbox" />
            <span>Open VLC low-latency transmit</span>
          </label>
          <button
            className="action-button"
            disabled={busyAction !== null || !outputStandard}
            onClick={() =>
              void runAction("Start assisted calibration", () =>
                startCalibrationSession({
                  output_standard: outputStandard,
                  run_calibration_first: runCalibrationFirst,
                  open_vlc_low_latency: openVlcLowLatency,
                }),
              )
            }
            type="button"
          >
            Start assisted calibration
          </button>
          <button
            className="action-button secondary"
            disabled={busyAction !== null || !state?.start.use_current_homography_enabled}
            onClick={() =>
              void runAction("Use current homography", () =>
                useCurrentHomography({
                  output_standard: outputStandard,
                  run_calibration_first: runCalibrationFirst,
                  open_vlc_low_latency: openVlcLowLatency,
                }),
              )
            }
            type="button"
          >
            Use current homography
          </button>
        </div>
      </div>
      <div className="metric-grid">
        <MetricCard label="Current step" value={String(state?.workflow.current_step ?? "start")} detail="React single surface" tone="accent" />
        <MetricCard label="Homography" value={String(homography.distortion_reference ?? "raw")} detail={`launch ready=${String(homography.launch_ready ?? false)}`} />
        <MetricCard label="Manual points" value={String(homography.manual_points_count ?? 0)} detail={`inliers=${String(homography.inliers_count ?? 0)}`} />
        <MetricCard
          label="Reprojection"
          value={Number(homography.mean_reprojection_error ?? 0).toFixed(3)}
          detail={`inlier ratio=${Number(homography.inlier_ratio ?? 0).toFixed(3)}`}
          tone="warn"
        />
      </div>
      <section className="panel">
        <div className="panel-title">Current calibration state</div>
        <div className="action-output">
          <div>Current step: {state?.workflow.current_step ?? "start"}</div>
          <div>Homography reference: {state?.workflow.homography_reference ?? "raw"}</div>
          <div>Use current homography: {state?.start.use_current_homography_enabled ? "enabled" : "disabled"}</div>
          <div>Manual pairs in session: {state?.workflow.manual_pair_count ?? 0}</div>
          <div>Accepting calibration does not start runtime. Live output still starts from Dashboard `Prepare` then `Start`.</div>
        </div>
      </section>
    </section>
  );
}
