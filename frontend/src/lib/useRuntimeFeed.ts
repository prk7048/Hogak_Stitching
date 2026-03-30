import { useEffect, useMemo, useState } from "react";

import {
  apiUrl,
  fetchGeometryArtifacts,
  fetchRuntimeState,
  normalizeRuntimeState,
  openRuntimeEventStream,
  type GeometryArtifactSummary,
  type RuntimeEvent,
  type RuntimeState,
} from "./api";

export function useRuntimeFeed() {
  const [state, setState] = useState<RuntimeState>({});
  const [artifacts, setArtifacts] = useState<GeometryArtifactSummary[]>([]);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [previewVersion, setPreviewVersion] = useState(0);
  const [streamState, setStreamState] = useState<"connecting" | "connected" | "offline">("connecting");

  async function refreshRuntimeSnapshot() {
    const [nextState, nextArtifacts] = await Promise.all([fetchRuntimeState(), fetchGeometryArtifacts()]);
    setState(normalizeRuntimeState(nextState));
    setArtifacts(nextArtifacts);
  }

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        await refreshRuntimeSnapshot();
      } catch {
        if (!cancelled) {
          setState((current) => current);
          setArtifacts((current) => current);
        }
      }
    };

    void load();
    const interval = window.setInterval(() => {
      void load();
      setPreviewVersion((value) => value + 1);
    }, 2500);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const source = openRuntimeEventStream((event) => {
      setEvents((current) => [event, ...current].slice(0, 40));
      setStreamState("connected");
      if (event.payload) {
        setState((current) => ({ ...current, ...normalizeRuntimeState(event.payload) }));
      }
    });

    if (!source) {
      setStreamState("offline");
      return;
    }

    setStreamState("connected");
    source.onerror = () => {
      setStreamState(source.readyState === EventSource.CLOSED ? "offline" : "connecting");
    };

    return () => {
      source.close();
    };
  }, []);

  const preview = useMemo(() => apiUrl(`/api/runtime/preview.jpg?ts=${previewVersion}`), [previewVersion]);

  return {
    state,
    artifacts,
    events,
    preview,
    previewVersion,
    streamState,
    refreshRuntime: refreshRuntimeSnapshot,
    refreshPreview: () => setPreviewVersion((value) => value + 1),
  };
}
