import type { Trade } from "@/lib/types";

function timeAgo(iso: string): string {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60)    return `${s}s`;
  if (s < 3600)  return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function triggerLabel(trigger: string): string {
  if (trigger === "session-open")  return "at open";
  if (trigger === "session-close") return "before close";
  return trigger;
}


const BORDER: Record<string, string> = {
  long:       "border-l-up",
  short:      "border-l-down",
  flat_long:  "border-l-down",   // closing a long = red action
  flat_short: "border-l-up",     // covering a short = green action
  flat:       "border-l-border", // fallback
  none:       "border-l-border",
};

export function FeedItem({ trade, fresh }: { trade: Trade; fresh?: boolean }) {
  // Filter out HOLD/CASH entries — no position change, nothing useful to show
  if (trade.position === "none" || trade.ticker === "CASH") return null;

  let posLabel = "";
  let posCls = "";
  
  const isSpot = trade.leverage <= 1.0;
  const levStr = trade.leverage > 1 ? `${trade.leverage}×` : "";

  if (trade.position === "flat") {
    // Determine what was closed. Use closedDirection if present (post-migration
    // records), otherwise infer from action: sell = was long, buy = was short.
    const closedDir = trade.closedDirection ?? (trade.action === "sell" ? "long" : "short");
    const closedLev = trade.closedLeverage ?? trade.leverage;
    const closedIsSpot = closedLev <= 1.0;
    const closedLevStr = closedLev > 1 ? `${closedLev}×` : "";

    if (closedDir === "long") {
      posLabel = closedIsSpot ? "SPOT SELL" : `CLOSE ${closedLevStr} LONG`;
      posCls   = "text-down";
    } else {
      posLabel = closedIsSpot ? "COVER SHORT" : `CLOSE ${closedLevStr} SHORT`;
      posCls   = "text-up";
    }
  } else if (isSpot && trade.position === "long") {
    // 1x long = true spot buy
    posLabel = "SPOT BUY";
    posCls = "text-up";
  } else if (trade.position === "long") {
    // Leveraged long: show Nx LONG
    posLabel = `${levStr} LONG`;
    posCls = "text-up";
  } else {
    // Short (any leverage): show Nx SHORT or just SHORT
    posLabel = levStr ? `${levStr} SHORT` : "SHORT";
    posCls = "text-down";
  }

  // Border color: for closes, derived from what was closed, not the position field
  let borderKey: string = trade.position;
  if (trade.position === "flat") {
    const closedDir = trade.closedDirection ?? (trade.action === "sell" ? "long" : "short");
    borderKey = closedDir === "long" ? "flat_long" : "flat_short";
  }
  const border = BORDER[borderKey] ?? BORDER.flat;

  return (
    <div className={`border-l-2 px-4 py-3 ${border} ${fresh ? "feed-fresh" : ""}`}>
      {/* Line 1: time · bot · LEVERAGEx POSITION TICKER price · when */}
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="num text-[10px] text-dim tabular-nums w-[20px]">
          {timeAgo(trade.timestamp)}
        </span>
        <span className="font-mono text-[11px] text-secondary font-medium">{trade.botName}</span>
        {levStr && (
          <span className="font-mono text-[10px] text-amber font-semibold">{levStr}</span>
        )}
        <span className={`font-mono text-[11px] font-semibold ${posCls}`}>{posLabel}</span>
        <span className="font-mono text-[12px] text-ink font-semibold">{trade.ticker}</span>
        {trade.price > 0 && (
          <span className="num text-[10px] text-secondary">
            @${trade.price.toFixed(2)}
          </span>
        )}
        <span className="font-mono text-[9px] tracking-widest text-dim uppercase">
          {triggerLabel(trade.trigger)}
        </span>
      </div>

      {/* Line 2: reasoning */}
      {trade.reasoning && (
        <p className="mt-1 text-[11px] text-dim leading-relaxed italic pl-[28px]">
          &ldquo;{trade.reasoning}&rdquo;
        </p>
      )}
    </div>
  );
}
