"use client";

import { useMemo } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip } from "recharts";
import type { AnalysisIdea, AnalysisOutcome } from "@/lib/types";

function resultFor(direction: string, change: number) {
  const normalized = direction.toLowerCase();
  if (normalized === "long") return change >= 0 ? "Pick aligned" : "Against the pick";
  if (normalized === "short") return change <= 0 ? "Pick aligned" : "Against the pick";
  return Math.abs(change) < 1 ? "Still developing" : "Price moved";
}

export function OutcomeTrend({ idea, outcome }: { idea: AnalysisIdea; outcome?: AnalysisOutcome }) {
  const data = useMemo(() => (outcome?.history ?? []).map((point) => ({ ...point, normalized: outcome?.initialPrice ? ((point.price / outcome.initialPrice) - 1) * 100 : 0 })), [outcome]);

  if (!outcome?.initialPrice || !outcome.currentPrice || outcome.returnPct === undefined || outcome.returnPct === null) {
    return <div className="outcome-empty">Outcome tracking for this pick will appear once market data is available.</div>;
  }

  const marketUp = outcome.returnPct >= 0;
  const direction = idea.suggested_direction.toLowerCase();
  const aligned = direction === "long" ? marketUp : direction === "short" ? !marketUp : null;
  const color = aligned === false ? "#f05252" : aligned === true ? "#2dd4bf" : marketUp ? "#8b8dff" : "#f5a524";
  const verdict = resultFor(direction, outcome.returnPct);

  return (
    <div className="outcome-panel">
      <div className="flex items-start justify-between gap-3">
        <div><div className="outcome-kicker">Since this pick</div><div className={`outcome-return ${marketUp ? "text-up" : "text-down"}`}>{marketUp ? "+" : ""}{outcome.returnPct.toFixed(2)}%</div></div>
        <span className={`outcome-verdict ${aligned === false ? "outcome-verdict-wrong" : ""}`}>{verdict}</span>
      </div>
      <div className="mt-2 h-11">
        {data.length > 1 ? <ResponsiveContainer width="100%" height="100%"><LineChart data={data} margin={{ top: 3, right: 2, bottom: 1, left: 2 }}><Tooltip cursor={false} content={({ active, payload }) => active && payload?.[0] ? <div className="outcome-tooltip">{payload[0].payload.date} · {Number(payload[0].value).toFixed(2)}%</div> : null} /><Line type="monotone" dataKey="normalized" stroke={color} strokeWidth={1.8} dot={false} activeDot={{ r: 3, fill: color, strokeWidth: 0 }} /></LineChart></ResponsiveContainer> : <div className="outcome-progress" style={{ backgroundColor: color }} />}
      </div>
      <div className="mt-1 flex items-center justify-between font-mono text-[9px] text-dim"><span>Then ${outcome.initialPrice.toFixed(2)}</span><span>Now ${outcome.currentPrice.toFixed(2)}</span></div>
    </div>
  );
}
