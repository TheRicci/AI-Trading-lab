"use client";

import { useEffect, useMemo, useRef } from "react";
import { ColorType, createChart, LineSeries, type UTCTimestamp } from "lightweight-charts";
import { botColorFor } from "@/lib/types";
import type { PortfolioHistoryByBot } from "@/lib/types";

type Point = { time: UTCTimestamp; value: number };

function seriesFor(history: PortfolioHistoryByBot) {
  return Object.entries(history).map(([name, points]) => {
    // deduplicate by exact second timestamp (lightweight-charts requires strictly ascending time)
    const uniquePoints = new Map<number, number>();
    for (const point of points) {
      const t = Math.floor(new Date(point.timestamp).getTime() / 1000);
      uniquePoints.set(t, point.totalValue);
    }
    const sortedTimes = Array.from(uniquePoints.keys()).sort((a, b) => a - b);
    return {
      name,
      points: sortedTimes.map((t) => ({ time: t as UTCTimestamp, value: uniquePoints.get(t)! })),
    };
  });
}

function LineChart({ history }: { history: PortfolioHistoryByBot }) {
  const host = useRef<HTMLDivElement>(null);
  const series = useMemo(() => seriesFor(history), [history]);

  useEffect(() => {
    const element = host.current;
    if (!element || !series.length) return;

    const chart = createChart(element, {
      width: element.clientWidth,
      height: 282,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#737383", fontFamily: "JetBrains Mono, monospace", fontSize: 10 },
      grid: { vertLines: { color: "rgba(255,255,255,0.025)" }, horzLines: { color: "rgba(255,255,255,0.055)" } },
      rightPriceScale: { borderColor: "#252530", scaleMargins: { top: 0.13, bottom: 0.10 } },
      timeScale: { borderColor: "#252530", timeVisible: true, ticksVisible: true },
      crosshair: { vertLine: { color: "rgba(139,141,255,0.38)", labelBackgroundColor: "#353655" }, horzLine: { color: "rgba(139,141,255,0.30)", labelBackgroundColor: "#353655" } },
      localization: { priceFormatter: (price: number) => `$${(price / 1000).toFixed(1)}k` },
    });

    series.forEach(({ name, points }) => {
      const color = botColorFor(name);
      // Remove lineType: 2 (curved) to prevent extreme bezier overshoot with sparse data
      const chartSeries = chart.addSeries(LineSeries, { color, lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerRadius: 4, crosshairMarkerBorderColor: "#111116", crosshairMarkerBackgroundColor: color });
      try {
        chartSeries.setData(points);
      } catch (e) {
        console.error("Failed to set data for", name, e);
      }
    });

    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() => chart.applyOptions({ width: element.clientWidth }));
    observer.observe(element);
    return () => { observer.disconnect(); chart.remove(); };
  }, [series]);

  return <div ref={host} className="h-[282px] w-full" />;
}

export function PortfolioChart({ history }: { history: PortfolioHistoryByBot }) {
  const names = Object.keys(history);
  const latest = Object.fromEntries(Object.entries(history).map(([name, points]) => [name, points.at(-1)?.totalValue ?? 100000]));

  if (!names.length) return <div className="card flex h-[280px] items-center justify-center"><p className="font-mono text-[11px] text-dim italic">No history yet — check back after the next bell.</p></div>;

  return <div className="card chart-shell p-5">
    <div className="mb-5"><div className="overline">Portfolio performance</div><div className="mt-1 text-[12px] text-secondary">Portfolio value across every bot.</div></div>
    <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1">{names.map((name) => { const change = (latest[name] / 100000 - 1) * 100; return <div key={name} className="chart-legend-item"><span className="chart-legend-dot" style={{ backgroundColor: botColorFor(name) }} /><span>{name.replace(/^open_/, "")}</span><strong className={change >= 0 ? "text-up" : "text-down"}>{change >= 0 ? "+" : ""}{change.toFixed(1)}%</strong></div>; })}</div>
    <LineChart history={history} />
  </div>;
}
