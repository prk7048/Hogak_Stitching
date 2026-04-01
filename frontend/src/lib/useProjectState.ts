import { useEffect, useRef, useState } from "react";

import { fetchProjectState, type ProjectState } from "./api";

const IDLE_POLL_MS = 5000;
const ACTIVE_POLL_MS = 1000;

function pollIntervalForState(state: ProjectState): number {
  const status = String(state.status || "").trim().toLowerCase();
  return status === "starting" || status === "running" ? ACTIVE_POLL_MS : IDLE_POLL_MS;
}

export function useProjectState() {
  const [state, setState] = useState<ProjectState>({});
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<number | null>(null);

  const refresh = async () => {
    const nextState = await fetchProjectState();
    setState(nextState);
    setLoading(false);
    return nextState;
  };

  useEffect(() => {
    let active = true;

    const schedule = (nextState: ProjectState) => {
      if (!active) {
        return;
      }
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(async () => {
        try {
          const refreshed = await refresh();
          schedule(refreshed);
        } catch {
          schedule(nextState);
        }
      }, pollIntervalForState(nextState));
    };

    void refresh()
      .then((nextState) => {
        schedule(nextState);
      })
      .catch(() => {
        setLoading(false);
        schedule({});
      });

    return () => {
      active = false;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, []);

  return {
    state,
    loading,
    refresh,
  };
}
