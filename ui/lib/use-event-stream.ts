"use client";

import { useEffect, useRef, useState } from "react";

import { eventStreamUrl } from "@/lib/api";
import type { TaskEvent } from "@/lib/types";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "idle";

interface Options {
  projectId: string | null;
  onEvent: (event: TaskEvent) => void;
  enabled?: boolean;
}

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;

export function useEventStream({
  projectId,
  onEvent,
  enabled = true,
}: Options): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>("idle");
  // Keep the latest callback in a ref so the effect doesn't tear down the
  // EventSource every render just because a new closure was created.
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    if (!enabled || !projectId) {
      setStatus("idle");
      return;
    }

    let closed = false;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    // Resume token — bumps as events arrive so we skip what we've already seen
    // on the next reconnect.
    let lastSeenId = 0;

    const connect = () => {
      if (closed) return;
      setStatus(attempt === 0 ? "connecting" : "reconnecting");
      const url = eventStreamUrl(projectId, lastSeenId);
      es = new EventSource(url);

      es.onopen = () => {
        if (closed) return;
        attempt = 0;
        setStatus("open");
      };

      es.onmessage = (msg) => {
        if (closed) return;
        try {
          const data = JSON.parse(msg.data) as TaskEvent;
          if (typeof data.id === "number" && data.id > lastSeenId) {
            lastSeenId = data.id;
          }
          handlerRef.current(data);
        } catch {
          // Malformed frame — skip it rather than tearing the stream down.
        }
      };

      es.onerror = () => {
        if (closed) return;
        // EventSource will attempt its own reconnect on a transport blip, but
        // we want deterministic exponential backoff keyed to `since_id`, so we
        // close and reopen ourselves.
        es?.close();
        es = null;
        const delay = Math.min(
          MAX_BACKOFF_MS,
          INITIAL_BACKOFF_MS * 2 ** Math.min(attempt, 5),
        );
        attempt += 1;
        setStatus("reconnecting");
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
      setStatus("idle");
    };
  }, [projectId, enabled]);

  return status;
}
