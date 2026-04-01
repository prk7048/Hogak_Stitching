import { useEffect, useReducer } from "react";

import {
  fetchRuntimeState,
  normalizeRuntimeState,
  openRuntimeEventStream,
  type RuntimeEvent,
  type RuntimeState,
} from "./api";

type FeedState = {
  state: RuntimeState;
  previewVersion: number;
  streamState: "connecting" | "connected" | "offline";
};

type FeedAction =
  | { type: "snapshot"; state: RuntimeState }
  | { type: "event"; event: RuntimeEvent }
  | { type: "stream"; streamState: FeedState["streamState"] };

const initialState: FeedState = {
  state: {},
  previewVersion: 0,
  streamState: "connecting",
};

function reducer(current: FeedState, action: FeedAction): FeedState {
  switch (action.type) {
    case "snapshot":
      return {
        ...current,
        state: normalizeRuntimeState(action.state),
        previewVersion: current.previewVersion + 1,
      };
    case "event": {
      const nextState = action.event.payload
        ? { ...current.state, ...normalizeRuntimeState(action.event.payload) }
        : current.state;
      const shouldRefreshPreview = action.event.type === "status";
      return {
        ...current,
        state: nextState,
        previewVersion: shouldRefreshPreview ? current.previewVersion + 1 : current.previewVersion,
      };
    }
    case "stream":
      return { ...current, streamState: action.streamState };
    default:
      return current;
  }
}

export function useRuntimeFeed() {
  const [feed, dispatch] = useReducer(reducer, initialState);

  async function refreshRuntimeSnapshot() {
    const nextState = await fetchRuntimeState();
    dispatch({ type: "snapshot", state: nextState });
  }

  useEffect(() => {
    void refreshRuntimeSnapshot().catch(() => undefined);
  }, []);

  useEffect(() => {
    const source = openRuntimeEventStream((event) => {
      dispatch({ type: "event", event });
      dispatch({ type: "stream", streamState: "connected" });
    });

    if (!source) {
      dispatch({ type: "stream", streamState: "offline" });
      return;
    }

    dispatch({ type: "stream", streamState: "connecting" });
    source.onerror = () => {
      dispatch({
        type: "stream",
        streamState: source.readyState === EventSource.CLOSED ? "offline" : "connecting",
      });
    };
    source.onopen = () => {
      dispatch({ type: "stream", streamState: "connected" });
    };

    return () => {
      source.close();
    };
  }, []);

  useEffect(() => {
    if (feed.streamState === "connected") {
      return;
    }
    const interval = window.setInterval(() => {
      void refreshRuntimeSnapshot().catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(interval);
  }, [feed.streamState]);

  return {
    state: feed.state,
    previewVersion: feed.previewVersion,
    streamState: feed.streamState,
    refreshRuntime: refreshRuntimeSnapshot,
  };
}
