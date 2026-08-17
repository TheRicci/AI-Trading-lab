"use client";

import { motion } from "framer-motion";
import { strategyFromBotName } from "@/lib/types";
import type { Bot } from "@/lib/types";

const STARTING = 100_000;
const BAR_MIN  = 94_000;
const BAR_MAX  = 115_000;

const PROVIDER_STRIP: Record<string, string> = {
  gemini: "provider-strip-gemini",
  groq:   "provider-strip-groq",
};

export function BotCard({ bot, rank }: { bot: Bot; rank: number }) {
  const value    = bot.totalValue ?? bot.cash;
  const pnl      = value - STARTING;
  const pnlPct   = (pnl / STARTING) * 100;
  const up       = pnl >= 0;
  const barPct   = Math.min(100, Math.max(0, ((value - BAR_MIN) / (BAR_MAX - BAR_MIN)) * 100));
  const strategy = strategyFromBotName(bot.botName);

  // Positions: count non-zero holdings, show S flag for any short (negative)
  const holdingEntries = Object.entries(bot.holdings ?? {}).filter(([, qty]) => qty !== 0);
  const hasShort = holdingEntries.some(([, qty]) => qty < 0);
  const positions = holdingEntries.length;
  const isLeveraged = bot.cash < 0; // negative cash = borrowed margin
  const strip    = PROVIDER_STRIP[bot.llmProvider] ?? "provider-strip-dim";

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay: rank * 0.04 }}
      className={`card mb-2 p-4 pl-5 ${strip} transition-colors duration-150 hover:bg-surface-hi`}
    >
      {/* Row 1: rank + name */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {/* Bot name */}
          <div className="num text-[12px] font-semibold text-ink truncate leading-tight">
            {bot.botName}
          </div>
          {/* Strategy + provider */}
          <div className="flex items-center gap-2 mt-0.5">
            <span className="font-mono text-[9px] tracking-[0.1em] uppercase text-dim">{strategy}</span>
            <span className="text-border">·</span>
            <span className={`font-mono text-[9px] tracking-[0.06em] uppercase ${
              bot.llmProvider === "gemini" ? "text-gemini" : "text-groq"
            }`}>
              {bot.llmProvider === "gemini" ? "Gemini" : "Qwen"}
            </span>
          </div>
        </div>
        {/* Rank badge */}
        <span className={`rank-badge flex-shrink-0 ${rank === 1 ? "rank-badge-1" : "rank-badge-n"}`}>
          {String(rank).padStart(2, "0")}
        </span>
      </div>

      {/* Row 2: value */}
      <div className={`num text-[22px] font-semibold mt-3 leading-none ${up ? "text-ink" : "text-down"}`}>
        ${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>

      {/* Row 3: PnL + positions */}
      <div className="flex items-center gap-3 mt-1">
        <span className={`num text-[11px] font-medium ${up ? "text-up" : "text-down"}`}>
          {up ? "▲" : "▼"} {up ? "+" : ""}{pnlPct.toFixed(2)}%
        </span>
        <span className={`num text-[11px] text-secondary`}>
          {up ? "+" : ""}${Math.abs(pnl).toLocaleString("en-US", { maximumFractionDigits: 0 })}
        </span>
        {positions > 0 && (
          <span className="font-mono text-[10px] text-dim">
            {positions} pos{positions !== 1 ? "s" : ""}
            {hasShort && <span className="text-down ml-1">S</span>}
            {isLeveraged && <span className="text-amber ml-1">L</span>}
          </span>
        )}
      </div>

      {/* Bar */}
      <div className="mt-3 h-[3px] rounded-full bg-border overflow-hidden">
        <motion.div
          className={`h-full rounded-full origin-left ${up ? "bg-up" : "bg-down"}`}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: barPct / 100 }}
          transition={{ duration: 0.7, delay: rank * 0.04 + 0.15, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
    </motion.div>
  );
}
