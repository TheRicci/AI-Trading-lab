"use client";

import { useMemo } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, ReferenceLine } from "recharts";
import type { AnalysisIdea, AnalysisOutcome } from "@/lib/types";

export function OutcomeTrend({ idea, outcome }: { idea: AnalysisIdea; outcome?: AnalysisOutcome }) {
  const data = useMemo(() => (outcome?.history ?? []).map((point) => ({ ...point, normalized: outcome?.initialPrice ? ((point.price / outcome.initialPrice) - 1) * 100 : 0 })), [outcome]);

  // No outcome at all or asset not supported by price feed
  if (!outcome?.initialPrice || !outcome.currentPrice || outcome.returnPct === undefined || outcome.returnPct === null) {
    return <div className="outcome-empty">Outcome tracking for this pick will appear once market data is available.</div>;
  }

  const marketUp = outcome.returnPct >= 0;
  const direction = idea.suggested_direction.toLowerCase();
  const aligned = direction === "long" ? marketUp : direction === "short" ? !marketUp : null;
  const color = aligned === false ? "#f05252" : aligned === true ? "#2dd4bf" : marketUp ? "#8b8dff" : "#f5a524";

  return (
    <div className="outcome-panel">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="outcome-kicker mb-1">Since this pick</div>
          <div className={`outcome-return ${marketUp ? "text-up" : "text-down"}`}>
            {marketUp ? "+" : ""}{outcome.returnPct.toFixed(2)}%
          </div>
        </div>
        <div className="text-right font-mono">
          <div className="text-[9px] text-dim mb-1">Then → Now</div>
          <div className="text-[12px] text-secondary">
            <span className="text-dim">${outcome.initialPrice.toFixed(2)}</span>
            <span className="text-dim mx-1.5">→</span>
            <span className={marketUp ? "text-up" : "text-down"}>${outcome.currentPrice.toFixed(2)}</span>
          </div>
        </div>
      </div>
      <div className="h-32">
        {data.length > 1
          ? <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data} margin={{ top: 4, right: 2, bottom: 4, left: 2 }}>
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
                <Tooltip cursor={false} content={({ active, payload }) => {
                  if (!active || !payload?.[0]) return null;
                  const raw: string = payload[0].payload.date ?? "";
                  const dt = new Date(raw.length === 16 ? raw + "Z" : raw);
                  const label = isNaN(dt.getTime()) ? raw : dt.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).replace(",", " ·");
                  return <div className="outcome-tooltip">{label} · {Number(payload[0].value).toFixed(2)}%</div>;
                }} />
                <Line type="monotone" dataKey="normalized" stroke={color} strokeWidth={2} dot={false} activeDot={{ r: 4, fill: color, strokeWidth: 0 }} />
              </LineChart>
            </ResponsiveContainer>
          : <div className="flex h-full items-center gap-3">
              <div className="outcome-progress flex-1" style={{ backgroundColor: color }} />
              <span className="font-mono text-[9px] text-dim whitespace-nowrap">
                {data.length === 0 ? "Awaiting candle data" : "First session"}
              </span>
            </div>
        }
      </div>
    </div>
  );
}
