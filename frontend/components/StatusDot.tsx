"use client";

import type { WsStatus } from "@/lib/useWebPubSub";

const DOT: Record<WsStatus, { color: string; label: string }> = {
  live:       { color: "bg-up",    label: "Live" },
  connecting: { color: "bg-amber", label: "Connecting" },
  polling:    { color: "bg-amber", label: "Polling" },
  error:      { color: "bg-down",  label: "Error" },
};

export function StatusDot({ status }: { status: WsStatus }) {
  const { color, label } = DOT[status];
  return (
    <div className="flex items-center gap-2">
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${color} animate-blink`} />
      <span className="font-mono text-[10px] tracking-[0.1em] text-dim uppercase">{label}</span>
    </div>
  );
}
