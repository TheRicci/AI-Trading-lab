"use client";

import { useEffect, useMemo, useRef } from "react";
import { ColorType, createChart, LineSeries, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { botColorFor, botDisplayName } from "@/lib/types";
import type { Bot, PortfolioHistoryByBot } from "@/lib/types";

type Point = { time: UTCTimestamp; value: number };

/** Truncate a timestamp string to midnight UTC and return Unix seconds. */
function toMidnightUTC(timestamp: string): number {
  const d = new Date(timestamp);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 1000;
}

/** Today at midnight UTC in Unix seconds. */
function todayMidnightUTC(): number {
  const now = new Date();
  return Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()) / 1000;
}

/** All calendar days (midnight UTC) between startDay and endDay inclusive. */
function dayRange(startDay: number, endDay: number): number[] {
  const days: number[] = [];
  const DAY = 86400;
  for (let d = startDay; d <= endDay; d += DAY) days.push(d);
  return days;
}

function seriesFor(history: PortfolioHistoryByBot, bots?: Bot[]) {
  const today = todayMidnightUTC();

  // Create a map of current live values from the leaderboard
  const liveBalances = new Map<string, number>();
  if (bots) {
    for (const b of bots) {
      if (b.botName && (b.totalValue != null || b.cash != null)) {
        liveBalances.set(b.botName, b.totalValue ?? b.cash);
      }
    }
  }

  // Include all bots from history plus any active bots in leaderboard
  const allNames = new Set(Object.keys(history));
  if (bots) {
    for (const b of bots) {
      if (b.botName) allNames.add(b.botName);
    }
  }

  return Array.from(allNames).map((name) => {
    const points = history[name] || [];

    // 1. Collapse to day granularity — keep the LAST value recorded on each day.
    const byDay = new Map<number, number>();
    for (const point of points) {
      const day = toMidnightUTC(point.timestamp);
      byDay.set(day, point.totalValue); // later entries overwrite earlier ones
    }

    let firstDay: number;
    let initialValue: number;

    if (byDay.size > 0) {
      const sortedDays = Array.from(byDay.keys()).sort((a, b) => a - b);
      firstDay = sortedDays[0];
      initialValue = byDay.get(firstDay)!;
    } else {
      // If a bot has no historical snapshots yet, start with today
      firstDay = today;
      const botObj = bots?.find((b) => b.botName === name);
      initialValue = liveBalances.get(name) ?? botObj?.startingCash ?? 100000;
      byDay.set(today, initialValue);
    }

    // 2. Forward-fill: for every calendar day up to today, carry the last known value.
    const allDays = dayRange(firstDay, today);
    let lastValue = initialValue;
    const filledPoints: Point[] = [];

    for (const day of allDays) {
      if (byDay.has(day)) {
        lastValue = byDay.get(day)!;
      }

      // If it's today and we have a live updated balance, use that live balance!
      // This makes the last chart data point move accordingly with the day's updated balance.
      // Once the day ends (day < today), it remains fixed with the final snapshot recorded for that day.
      const isToday = day === today;
      const pointValue = isToday && liveBalances.has(name)
        ? liveBalances.get(name)!
        : lastValue;

      filledPoints.push({ time: day as UTCTimestamp, value: pointValue });
    }

    return { name, points: filledPoints };
  });
}

function LineChart({ history, bots }: { history: PortfolioHistoryByBot; bots?: Bot[] }) {
  const host = useRef<HTMLDivElement>(null);
  const series = useMemo(() => seriesFor(history, bots), [history, bots]);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesMapRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());

  // Initialize chart once
  useEffect(() => {
    const element = host.current;
    if (!element) return;

    const chart = createChart(element, {
      width: element.clientWidth,
      height: 282,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#737383", fontFamily: "JetBrains Mono, monospace", fontSize: 10 },
      grid: { vertLines: { color: "rgba(255,255,255,0.025)" }, horzLines: { color: "rgba(255,255,255,0.055)" } },
      rightPriceScale: { borderColor: "#252530", scaleMargins: { top: 0.13, bottom: 0.10 } },
      timeScale: { borderColor: "#252530", timeVisible: false, ticksVisible: true },
      crosshair: { vertLine: { color: "rgba(139,141,255,0.38)", labelBackgroundColor: "#353655" }, horzLine: { color: "rgba(139,141,255,0.30)", labelBackgroundColor: "#353655" } },
      localization: { priceFormatter: (price: number) => `$${(price / 1000).toFixed(1)}k` },
    });

    chartRef.current = chart;

    const observer = new ResizeObserver(() => {
      if (element && chartRef.current) {
        chartRef.current.applyOptions({ width: element.clientWidth });
      }
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesMapRef.current.clear();
    };
  }, []);

  // Update series data when `series` changes
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !series.length) return;

    const existingMap = seriesMapRef.current;
    const currentNames = new Set(series.map((s) => s.name));

    // Remove any series no longer in data
    for (const [name, chartSeries] of existingMap.entries()) {
      if (!currentNames.has(name)) {
        chart.removeSeries(chartSeries);
        existingMap.delete(name);
      }
    }

    // Add or update series
    series.forEach(({ name, points }) => {
      let chartSeries = existingMap.get(name);
      if (!chartSeries) {
        const color = botColorFor(name);
        chartSeries = chart.addSeries(LineSeries, {
          color,
          lineWidth: 2,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerRadius: 4,
          crosshairMarkerBorderColor: "#111116",
          crosshairMarkerBackgroundColor: color,
        });
        existingMap.set(name, chartSeries);
      }
      try {
        chartSeries.setData(points);
      } catch (e) {
        console.error("Failed to set data for", name, e);
      }
    });

    chart.timeScale().fitContent();
  }, [series]);

  return <div ref={host} className="h-[282px] w-full" />;
}

export function PortfolioChart({ history, bots }: { history: PortfolioHistoryByBot; bots?: Bot[] }) {
  const names = useMemo(() => {
    const set = new Set(Object.keys(history));
    if (bots) {
      for (const b of bots) if (b.botName) set.add(b.botName);
    }
    return Array.from(set);
  }, [history, bots]);

  const latest = useMemo(() => {
    const map: Record<string, number> = {};
    for (const name of names) {
      const liveBot = bots?.find((b) => b.botName === name);
      const liveVal = liveBot?.totalValue ?? liveBot?.cash;
      const histVal = history[name]?.at(-1)?.totalValue;
      map[name] = liveVal ?? histVal ?? 100000;
    }
    return map;
  }, [names, history, bots]);

  if (!names.length) {
    return (
      <div className="card flex h-[280px] items-center justify-center">
        <p className="font-mono text-[11px] text-dim italic">No history yet — check back after the next bell.</p>
      </div>
    );
  }

  return (
    <div className="card chart-shell p-5">
      <div className="mb-5">
        <div className="mt-1 text-[12px] text-secondary">Portfolio value across every bot.</div>
      </div>
      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1">
        {names.map((name) => {
          const currentVal = latest[name];
          const startingCash = bots?.find((b) => b.botName === name)?.startingCash ?? 100000;
          const change = ((currentVal / startingCash) - 1) * 100;
          return (
            <div key={name} className="chart-legend-item">
              <span className="chart-legend-dot" style={{ backgroundColor: botColorFor(name) }} />
              <span>{botDisplayName(name)}</span>
              <strong className={change >= 0 ? "text-up" : "text-down"}>
                {change >= 0 ? "+" : ""}{change.toFixed(1)}%
              </strong>
            </div>
          );
        })}
      </div>
      <LineChart history={history} bots={bots} />
    </div>
  );
}
