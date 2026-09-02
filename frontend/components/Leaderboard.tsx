import { BotCard } from "./BotCard";
import type { Bot } from "@/lib/types";

export function Leaderboard({ bots }: { bots: Bot[] }) {
  if (bots.length === 0) {
    return (
      <div className="card p-8 text-center">
        <span className="font-mono text-[11px] text-dim">Loading…</span>
      </div>
    );
  }

  const sorted  = [...bots].sort(
    (a, b) => (b.totalValue ?? b.cash) - (a.totalValue ?? a.cash)
  );
  const allNavs = sorted.map((b) => b.totalValue ?? b.cash);

  return (
    <div>
      {sorted.map((bot, i) => (
        <BotCard key={bot.botName} bot={bot} rank={i + 1} allNavs={allNavs} />
      ))}
    </div>
  );
}
