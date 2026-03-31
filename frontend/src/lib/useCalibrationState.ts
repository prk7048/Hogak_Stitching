import { useEffect, useState } from "react";

import { fetchCalibrationState, type CalibrationState } from "./api";

type Loader = () => Promise<CalibrationState>;

export function useCalibrationState(loader: Loader = fetchCalibrationState) {
  const [state, setState] = useState<CalibrationState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const nextState = await loader();
      setState(nextState);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, [loader]);

  return {
    state,
    loading,
    error,
    setState,
    refresh,
  };
}
