"use client";

import { motion } from "framer-motion";
import { botColorFor, MODEL_SHORT, MODEL_PROVIDER_LABEL, strategyFromBotName } from "@/lib/types";
import type { Bot } from "@/lib/types";

export function BotCard({
  bot,
  rank,
  allNavs,
}: {
  bot: Bot;
  rank: number;
  allNavs: number[];
}) {
  const starting  = bot.startingCash ?? 100_000;
  const value     = bot.totalValue ?? bot.cash;
  
  const pnl       = value - starting;
  const pnlPct    = (pnl / starting) * 100;
  const up        = pnl >= 0;

  const color         = botColorFor(bot.botName);
  const modelShort    = MODEL_SHORT[bot.llmModel] ?? bot.llmModel;
  const providerLabel = MODEL_PROVIDER_LABEL[bot.llmModel] ?? bot.llmProvider;

  // Classify positions
  const positionEntries = Object.entries(bot.positions ?? {});
  const isLeveraged     = positionEntries.some(([, p]) => p.leverage > 1);
  const spotPositions       = positionEntries.filter(([, p]) => p.leverage <= 1.0);
  const leveragedPositions  = positionEntries.filter(([, p]) => p.leverage > 1.0);

  // Spot value = sum of spot notionals (marked at current price if available, else entry)
  const spotValue = spotPositions.reduce((s, [, p]) => {
    const price = p.currentPrice ?? p.entryPrice;
    return s + Math.abs(p.shares) * price;
  }, 0);

  // Leveraged notional value
  const leveragedNotional = leveragedPositions.reduce((s, [, p]) => {
    const price = p.currentPrice ?? p.entryPrice;
    return s + Math.abs(p.shares) * price;
  }, 0);

  const cashBalance = bot.availableMargin;

  // Center-origin P&L bar logic & "Portfolio" row
  const netUnrealized = positionEntries.reduce((sum, [, p]) => sum + (p.unrealizedPnl || 0), 0);
  const netUnrealizedPct = (netUnrealized / value) * 100;
  const netUp = netUnrealized >= 0;
  
  // Scale max visual swing to 5% of current portfolio value (so a 5% unrealized swing fills half the bar)
  const maxSwing = value * 0.05; 
  const clampedUnrealized = Math.max(Math.min(netUnrealized, maxSwing), -maxSwing);
  const barWidthPct = maxSwing > 0 ? (Math.abs(clampedUnrealized) / maxSwing) * 50 : 0;
  const barLeftPct = netUp ? 50 : 50 - barWidthPct;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay: rank * 0.04 }}
      className="card bot-card mb-2 p-4 pl-5 transition-colors duration-150 hover:bg-surface-hi"
      style={{ borderLeftColor: color }}
    >
      {/* Row 1: model name + session badge + rank */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="num text-[13px] font-semibold truncate leading-tight" style={{ color }}>
              {modelShort}
            </span>
            <span
              className={`font-mono text-[9px] px-1.5 py-0.5 rounded border uppercase tracking-wider ${
                bot.botName.startsWith("close")
                  ? "border-amber-500/30 text-amber-400 bg-amber-500/10"
                  : "border-sky-500/30 text-sky-400 bg-sky-500/10"
              }`}
            >
              {bot.botName.startsWith("close") ? "Close" : "Open"}
            </span>
          </div>
          <div className="flex items-center gap-1.5 font-mono text-[9px] tracking-[0.08em] uppercase text-dim mt-0.5">
            <span>{strategyFromBotName(bot.botName)}</span>
            <span>·</span>
            <span>{providerLabel}</span>
          </div>
        </div>
        <span className={`rank-badge flex-shrink-0 ${rank === 1 ? "rank-badge-1" : "rank-badge-n"}`}>
          {String(rank).padStart(2, "0")}
        </span>
      </div>

      {/* Row 2: NAV */}
      <div className={`num text-[22px] font-semibold mt-3 leading-none ${up ? "text-ink" : "text-down"}`}>
        ${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>

      {/* Row 3: P&L (All-time and Portfolio) */}
      <div className="mt-2 space-y-1">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-[9px] text-dim uppercase w-16 tracking-wider">All-time</span>
            <span className={`num text-[11px] font-medium ${up ? "text-up" : "text-down"}`}>
              {up ? "▲" : "▼"} {up ? "+" : "-"}{Math.abs(pnlPct).toFixed(2)}%
            </span>
            <span className="num text-[11px] text-secondary">
              {up ? "+" : "-"}${Math.abs(pnl).toLocaleString("en-US", { maximumFractionDigits: 0 })}
            </span>
          </div>
          {isLeveraged && (
            <span className="flex gap-1">
              <span className="font-mono text-[9px] text-amber px-1 border border-amber/30 rounded">LEV</span>
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[9px] text-dim uppercase w-16 tracking-wider">Portfolio</span>
          <span className={`num text-[11px] font-medium ${netUp ? "text-up" : "text-down"}`}>
            {netUp ? "▲" : "▼"} {netUp ? "+" : "-"}{Math.abs(netUnrealizedPct).toFixed(2)}%
          </span>
          <span className="num text-[11px] text-secondary">
            {netUp ? "+" : "-"}${Math.abs(netUnrealized).toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </span>
        </div>
      </div>

      {/* Per-position rows */}
      {positionEntries.length > 0 && (
        <div className="mt-3 space-y-2">
          {positionEntries.map(([ticker, pos]) => {
            const isSpot   = pos.leverage <= 1.0;
            const isLong   = pos.direction === "long";
            const pnl      = pos.unrealizedPnl;
            const pnlUp    = pnl == null || pnl >= 0;
            
            // Re-calculate pnlPct relative to the position entry (notional size) instead of total portfolio
            const posPnlPct = pnl != null && pos.notional > 0 ? (pnl / pos.notional) * 100 : undefined;
            
            // Current Balance = Current Price * Shares (fallback to notional if currentPrice is missing)
            const currentBalance = pos.currentPrice != null 
                ? Math.abs(pos.shares) * pos.currentPrice 
                : pos.notional + (pnl || 0);

            const levStr   = `${pos.leverage}×`;
            // SPOT = 1x long only. Shorts always show leverage. Longs with leverage show Nx LONG.
            const dirLabel = isLong && isSpot
              ? "SPOT"
              : `${isSpot ? "" : levStr + " "}${pos.direction.toUpperCase()}`;
            return (
              <div key={ticker} className="bg-surface-hi rounded px-3 py-2 text-[11px]">
                {/* Direction badge + ticker */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`font-mono font-semibold text-[10px] px-1.5 py-0.5 rounded ${isLong ? "bg-up/10 text-up" : "bg-down/10 text-down"}`}>
                      {dirLabel}
                    </span>
                    <span className="font-mono font-semibold text-ink">{ticker}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="num font-semibold text-ink">
                      ${currentBalance.toLocaleString("en-US", { maximumFractionDigits: 0 })}
                    </span>
                    {pnl != null && (
                      <span className={`num font-medium ${pnlUp ? "text-up" : "text-down"}`}>
                        ({pnlUp ? "+" : "-"}${Math.abs(pnl).toLocaleString("en-US", { maximumFractionDigits: 0 })})
                      </span>
                    )}
                  </div>
                </div>
                {/* Entry → Current price */}
                <div className="flex items-center justify-between mt-1 text-dim">
                  <span>
                    Entry ${pos.entryPrice.toFixed(2)}
                    {pos.currentPrice != null && (
                      <> → <span className={pnlUp ? "text-up" : "text-down"}>${pos.currentPrice.toFixed(2)}</span></>
                    )}
                  </span>
                  {posPnlPct != null && (
                    <span className={`num text-[10px] ${pnlUp ? "text-up" : "text-down"}`}>
                      {pnlUp ? "+" : "-"}{Math.abs(posPnlPct).toFixed(2)}%
                    </span>
                  )}
                </div>
                {/* Size / Margin row */}
                <div className="mt-1 text-[10px] text-dim">
                  Size: ${pos.notional.toLocaleString("en-US", { maximumFractionDigits: 0 })}
                  {!isSpot && (
                    <span className="ml-1">
                      (Margin used: ${pos.marginUsed.toLocaleString("en-US", { maximumFractionDigits: 0 })})
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Portfolio breakdown: Spot / Leveraged / Cash */}
      <div className="mt-3 pt-2 border-t border-border grid grid-cols-3 gap-1 text-[10px]">
        <div className="text-center">
          <div className="text-dim mb-0.5">Spot</div>
          <div className="num text-ink font-medium">
            ${spotValue > 0 ? spotValue.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—"}
          </div>
        </div>
        <div className="text-center">
          <div className="text-dim mb-0.5">Leveraged</div>
          <div className="num text-amber font-medium">
            ${leveragedNotional > 0 ? leveragedNotional.toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—"}
          </div>
        </div>
        <div className="text-center">
          <div className="text-dim mb-0.5">Cash</div>
          <div className={`num font-medium ${cashBalance >= 0 ? "text-ink" : "text-down"}`}>
            ${cashBalance.toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </div>
        </div>
      </div>

      {/* Center-origin P&L Bar */}
      <div className="mt-3 relative h-[3px] rounded-full bg-border overflow-hidden">
        {/* Center tick mark */}
        <div className="absolute left-1/2 top-0 bottom-0 w-[1px] bg-surface-hi z-10" />
        <motion.div
          className="absolute h-full rounded-full"
          style={{ backgroundColor: netUp ? "#10b981" : "#ef4444" }}
          initial={{ left: "50%", width: "0%" }}
          animate={{ left: `${barLeftPct}%`, width: `${barWidthPct}%` }}
          transition={{ duration: 0.7, delay: rank * 0.04 + 0.15, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
    </motion.div>
  );
}

