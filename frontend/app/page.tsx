"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getLeaderboard, getPortfolioHistory, getTrades } from "@/lib/api";
import { useWebPubSub } from "@/lib/useWebPubSub";
import { StatusDot } from "@/components/StatusDot";
import { Leaderboard } from "@/components/Leaderboard";
import { PortfolioChart } from "@/components/PortfolioChart";
import { LiveFeed } from "@/components/LiveFeed";
import type { Bot, LiveTradeMessage, PortfolioHistoryByBot, Trade } from "@/lib/types";

export default function TradingFloorPage() {
  const [bots,    setBots]    = useState<Bot[]>([]);
  const [trades,  setTrades]  = useState<Trade[]>([]);
  const [history, setHistory] = useState<PortfolioHistoryByBot>({});
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
  const [loadError, setLoadError] = useState(false);
  const timers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const loadAll = useCallback(async () => {
    try {
      const [b, t, h] = await Promise.all([getLeaderboard(), getTrades(), getPortfolioHistory()]);
      setBots(b); setTrades(t); setHistory(h); setLoadError(false);
    } catch { setLoadError(true); }
  }, []);

  useEffect(() => {
    loadAll();
    const iv = setInterval(loadAll, 60_000);
    return () => clearInterval(iv);
  }, [loadAll]);

  const handleLiveTrade = useCallback((trade: LiveTradeMessage) => {
    setTrades((prev) => [trade, ...prev.filter((t) => t.id !== trade.id)]);
    setFreshIds((prev) => new Set(prev).add(trade.id));
    const timer = setTimeout(() => {
      setFreshIds((prev) => { const n = new Set(prev); n.delete(trade.id); return n; });
      timers.current.delete(trade.id);
    }, 4000);
    timers.current.set(trade.id, timer);
    setTimeout(loadAll, 800);
  }, [loadAll]);

  const status = useWebPubSub(handleLiveTrade);

  useEffect(() => () => { timers.current.forEach((t) => clearTimeout(t)); }, []);

  // Derived stats
  const totalCapital = bots.reduce((s, b) => s + (b.totalValue ?? b.cash), 0);
  const leader       = [...bots].sort((a, b) => (b.totalValue ?? b.cash) - (a.totalValue ?? a.cash))[0];
  const leaderPnl    = leader ? ((leader.totalValue ?? leader.cash) - (leader.startingCash ?? 100_000)) / (leader.startingCash ?? 100_000) * 100 : 0;

  if (loadError && !bots.length) {
    return (
      <div className="card p-8 text-center">
        <p className="font-mono text-[11px] text-dim">
          Can&rsquo;t reach the API. Set <code className="text-amber">NEXT_PUBLIC_API_BASE</code> or{" "}
          <code className="text-amber">NEXT_PUBLIC_MOCK=true</code>.
        </p>
      </div>
    );
  }

  return (
    <div>
      {/* ── Data ribbon ───────────────────────────────────────────────────── */}
      <div className="card mb-8 overflow-hidden">
        <div className="data-ribbon">
          <div className="ribbon-item">
            <span className="ribbon-label">Total capital</span>
            <span className="ribbon-value num">
              ${totalCapital.toLocaleString("en-US", { maximumFractionDigits: 0 })}
            </span>
          </div>
          <div className="ribbon-item">
            <span className="ribbon-label">Decisions</span>
            <span className="ribbon-value num">{trades.length}</span>
          </div>
          {leader && (
            <div className="ribbon-item">
              <span className="ribbon-label">Leader</span>
              <span className="ribbon-value num">{leader.botName}</span>
              <span className={`num text-[11px] ml-1 ${leaderPnl >= 0 ? "text-up" : "text-down"}`}>
                {leaderPnl >= 0 ? "▲" : "▼"}&nbsp;{Math.abs(leaderPnl).toFixed(2)}%
              </span>
            </div>
          )}
          <div className="ribbon-item">
            <span className="ribbon-label">Feed</span>
            <StatusDot status={status} />
          </div>
        </div>
      </div>

      {/* ── Main grid ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[360px_1fr] lg:items-start">

        {/* Left — Leaderboard */}
        <section>
          <div className="section-head mb-4">
            <span className="overline">Leaderboard</span>
          </div>
          <Leaderboard bots={bots} />
        </section>

        {/* Right — Chart + Feed */}
        <section className="space-y-8">
          <div>
            <div className="section-head mb-4">
              <span className="overline">Portfolio Performance</span>
            </div>
            <PortfolioChart history={history} />
          </div>

          <div>
            <div className="flex items-center justify-between mb-4">
              <div className="section-head mb-0">
                <span className="overline">Live Reasoning Feed</span>
              </div>
              <span className="num text-[10px] text-dim">{trades.length} decisions</span>
            </div>
            <LiveFeed trades={trades} freshIds={freshIds} />
          </div>
        </section>
      </div>
    </div>
  );
}
