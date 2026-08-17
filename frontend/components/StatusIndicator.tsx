"use client";

export function StatusIndicator({ status }: { status: "connecting" | "live" | "disconnected" }) {
  const label =
    status === "live" ? "live" : status === "connecting" ? "connecting..." : "disconnected -- retrying";

  return (
    <div className="flex items-center gap-2 font-mono text-xs text-dim">
      <span className="relative flex h-[7px] w-[7px]">
        {status === "live" && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-up opacity-60" />
        )}
        <span
          className={`relative inline-flex h-[7px] w-[7px] rounded-full ${
            status === "live" ? "bg-up" : "bg-dim"
          }`}
        />
      </span>
      <span>{label}</span>
    </div>
  );
}
