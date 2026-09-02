"use client";

import { useEffect, useRef, useState } from "react";
import { getNegotiateUrl, IS_MOCK } from "./api";
import type { LiveTradeMessage } from "./types";

export type WsStatus = "connecting" | "live" | "polling" | "error";

export function useWebPubSub(onTrade: (trade: LiveTradeMessage) => void): WsStatus {
  const [status, setStatus] = useState<WsStatus>(IS_MOCK ? "polling" : "connecting");
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (IS_MOCK) {
      setStatus("polling");
      return;
    }
    let cancelled = false;
    async function connect() {
      try {
        const url = await getNegotiateUrl();
        if (!url || cancelled) { setStatus("polling"); return; }
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onopen = () => !cancelled && setStatus("live");
        ws.onerror = () => !cancelled && setStatus("error");
        ws.onclose = () => !cancelled && setStatus("polling");
        ws.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data as string) as LiveTradeMessage;
            if (msg.type === "trade") onTrade(msg);
          } catch { /* ignore malformed messages */ }
        };
      } catch { if (!cancelled) setStatus("polling"); }
    }
    connect();
    return () => { cancelled = true; wsRef.current?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return status;
}
