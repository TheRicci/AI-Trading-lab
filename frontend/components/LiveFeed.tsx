"use client";

import { AnimatePresence, motion } from "framer-motion";
import { FeedItem } from "./FeedItem";
import type { Trade } from "@/lib/types";

export function LiveFeed({ trades, freshIds }: { trades: Trade[]; freshIds: Set<string> }) {
  if (!trades.length) {
    return (
      <div className="card px-4 py-8 text-center">
        <p className="font-mono text-[11px] text-dim italic">No trades logged yet.</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden divide-y divide-border">
      <AnimatePresence initial={false}>
        {trades.map((trade) => (
          <motion.div
            key={trade.id}
            layout
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.25, ease: "easeOut" }}
          >
            <FeedItem trade={trade} fresh={freshIds.has(trade.id)} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
