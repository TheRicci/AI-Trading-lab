import type { Trade } from "@/lib/types";

function timeAgo(iso: string): string {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60)    return `${s}s`;
  if (s < 3600)  return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

// Map trigger string → human-readable label
function triggerLabel(trigger: string): string {
  if (trigger === "session-open")  return "at open";
  if (trigger === "session-close") return "before close";
  return trigger;
}

// Action display — show BUY/SELL/HOLD, and annotate if shares went negative (short)
function actionDisplay(action: string): { label: string; cls: string } {
  switch (action) {
    case "buy":  return { label: "BUY",  cls: "text-up"   };
    case "sell": return { label: "SELL", cls: "text-down" };
    default:     return { label: "HOLD", cls: "text-dim"  };
  }
}

const BORDER: Record<string, string> = {
  buy:  "border-l-up",
  sell: "border-l-down",
  hold: "border-l-border",
};

export function FeedItem({ trade, fresh }: { trade: Trade; fresh?: boolean }) {
  const { label: actionLabel, cls: actionCls } = actionDisplay(trade.action);
  const border = BORDER[trade.action] ?? BORDER.hold;

  return (
    <div className={`border-l-2 px-4 py-3 ${border} ${fresh ? "feed-fresh" : ""}`}>
      {/* Line 1: time · bot · ACTION TICKER qty@price · when */}
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="num text-[10px] text-dim tabular-nums w-[20px]">
          {timeAgo(trade.timestamp)}
        </span>
        <span className="font-mono text-[11px] text-secondary font-medium">{trade.botName}</span>
        <span className={`font-mono text-[11px] font-semibold ${actionCls}`}>{actionLabel}</span>
        <span className="font-mono text-[12px] text-ink font-semibold">{trade.ticker}</span>
        {trade.shares > 0 && (
          <span className="num text-[10px] text-secondary">
            {trade.shares.toLocaleString()}@${trade.price.toFixed(2)}
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
