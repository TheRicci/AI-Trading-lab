"use client";

import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PROVIDER_HEX } from "@/lib/types";
import type { Bot, PortfolioHistoryByBot } from "@/lib/types";

interface ChartRow {
  timestamp: number;
  [botName: string]: number;
}

// Bots don't necessarily snapshot at the exact same millisecond (each
// processes its Event Grid delivery independently), so this merges every
// bot's history onto one shared, sorted timeline and forward-fills gaps
// -- otherwise recharts would only draw a point where every series
// happens to line up exactly, which is almost never.
function mergeHistories(history: PortfolioHistoryByBot): ChartRow[] {
  const botNames = Object.keys(history);
  const allTimestamps = Array.from(
    new Set(botNames.flatMap((name) => history[name].map((p) => p.timestamp)))
  ).sort();

  const lastKnown: Record<string, number> = {};
  return allTimestamps.map((ts) => {
    const row: ChartRow = { timestamp: new Date(ts).getTime() };
    for (const name of botNames) {
      const point = history[name].find((p) => p.timestamp === ts);
      if (point) lastKnown[name] = point.totalValue;
      if (lastKnown[name] !== undefined) row[name] = lastKnown[name];
    }
    return row;
  });
}

export function ComparisonChart({ history, bots }: { history: PortfolioHistoryByBot; bots: Bot[] }) {
  const data = useMemo(() => mergeHistories(history), [history]);
  const botNames = Object.keys(history);

  if (botNames.length === 0 || data.length === 0) {
    return (
      <div className="flex h-[260px] items-center justify-center rounded border border-border bg-surface">
        <p className="text-sm italic text-dim">No history yet -- check back after the next bell.</p>
      </div>
    );
  }

  const colorFor = (name: string) => {
    const bot = bots.find((b) => b.botName === name);
    return (bot && PROVIDER_HEX[bot.llmProvider]) || "#948d80";
  };

  return (
    <div className="h-[260px] rounded border border-border bg-surface p-4">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid stroke="#34302a" strokeDasharray="3 3" />
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
            stroke="#948d80"
            fontSize={11}
            fontFamily="var(--font-mono)"
          />
          <YAxis
            tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
            stroke="#948d80"
            fontSize={11}
            fontFamily="var(--font-mono)"
            width={50}
          />
          <Tooltip
            contentStyle={{ background: "#1d1b18", border: "1px solid #34302a", fontFamily: "var(--font-mono)" }}
            labelFormatter={(v) => new Date(v).toLocaleString()}
            formatter={(v: number) => [`$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, ""]}
          />
          <Legend wrapperStyle={{ fontFamily: "var(--font-mono)", fontSize: 11 }} />
          {botNames.map((name) => (
            <Line
              key={name}
              type="monotone"
              dataKey={name}
              stroke={colorFor(name)}
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
