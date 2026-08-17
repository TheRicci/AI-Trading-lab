"use client";

import { useMemo } from "react";
import {
  Area, AreaChart, CartesianGrid, Legend,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { PROVIDER_HEX } from "@/lib/types";
import type { Bot, PortfolioHistoryByBot } from "@/lib/types";

// Provider hex overrides to match new palette
const HEX: Record<string, string> = {
  gemini: "#7ba7f5",
  groq:   "#f5945a",
};

interface ChartRow { timestamp: number; [k: string]: number; }

function merge(history: PortfolioHistoryByBot): ChartRow[] {
  const names = Object.keys(history);
  const allTs = Array.from(
    new Set(names.flatMap((n) => history[n].map((p) => p.timestamp)))
  ).sort();
  const last: Record<string, number> = {};
  return allTs.map((ts) => {
    const row: ChartRow = { timestamp: new Date(ts).getTime() };
    for (const name of names) {
      const pt = history[name].find((p) => p.timestamp === ts);
      if (pt) last[name] = pt.totalValue;
      if (last[name] !== undefined) row[name] = last[name];
    }
    return row;
  });
}

function Tip({ active, payload, label }: {
  active?: boolean;
  payload?: { name: string; value: number; color: string }[];
  label?: number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="card px-4 py-3 min-w-[200px] shadow-md">
      <div className="overline mb-2">
        {new Date(label ?? 0).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
      </div>
      {[...payload].sort((a, b) => b.value - a.value).map((p) => (
        <div key={p.name} className="flex items-center justify-between gap-4 py-[3px]">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: p.color }} />
            <span className="font-mono text-[10px] text-secondary truncate max-w-[100px]">{p.name}</span>
          </div>
          <span className="num text-[11px] text-ink font-medium">
            ${p.value.toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </span>
        </div>
      ))}
    </div>
  );
}

export function PortfolioChart({ history, bots }: { history: PortfolioHistoryByBot; bots: Bot[] }) {
  const data  = useMemo(() => merge(history), [history]);
  const names = Object.keys(history);

  if (!names.length || !data.length) {
    return (
      <div className="card flex h-[280px] items-center justify-center">
        <p className="font-mono text-[11px] text-dim italic">No history yet — check back after the next bell.</p>
      </div>
    );
  }

  const colorFor = (name: string) => {
    const bot = bots.find((b) => b.botName === name);
    return (bot && HEX[bot.llmProvider]) ?? PROVIDER_HEX[bot?.llmProvider ?? ""] ?? "#4e4e5e";
  };

  return (
    <div className="card p-5 h-[300px]">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, left: 0, bottom: 0 }}>
          <defs>
            {names.map((name) => {
              const c = colorFor(name);
              return (
                <linearGradient key={name} id={`g-${name}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%"   stopColor={c} stopOpacity={0.18} />
                  <stop offset="100%" stopColor={c} stopOpacity={0}    />
                </linearGradient>
              );
            })}
          </defs>

          <CartesianGrid
            stroke="rgba(255,255,255,0.04)"
            strokeDasharray="3 6"
            horizontal={true}
            vertical={false}
          />
          <XAxis
            dataKey="timestamp"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) =>
              new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" })
            }
            tick={{ fill: "#4e4e5e", fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}
            tickLine={false}
            axisLine={{ stroke: "#252530" }}
          />
          <YAxis
            tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
            tick={{ fill: "#4e4e5e", fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}
            tickLine={false}
            axisLine={false}
            width={44}
          />
          <Tooltip content={<Tip />} cursor={{ stroke: "#252530", strokeWidth: 1 }} />
          <Legend
            wrapperStyle={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: "#4e4e5e", paddingTop: 8 }}
          />
          {names.map((name) => (
            <Area
              key={name}
              type="monotone"
              dataKey={name}
              stroke={colorFor(name)}
              strokeWidth={1.5}
              fill={`url(#g-${name})`}
              dot={false}
              connectNulls
              activeDot={{ r: 3, strokeWidth: 0 }}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
