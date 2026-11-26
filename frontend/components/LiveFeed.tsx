"use client";

import { useState, useRef, useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { FeedItem } from "./FeedItem";
import type { Trade } from "@/lib/types";

export function LiveFeed({ trades, freshIds }: { trades: Trade[]; freshIds: Set<string> }) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(15);
  const containerRef = useRef<HTMLDivElement>(null);

  // Filter out HOLD / CASH / none entries BEFORE pagination so ghost items don't consume visible slots
  const displayableTrades = trades.filter(
    (t) => t.position !== "none" && t.ticker !== "CASH" && t.action !== "hold"
  );
  
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const measureAndSet = (height: number) => {
      if (height <= 0) return;
      // Average FeedItem is ~70px. Floor it so trades fill the entire vertical space
      const newSize = Math.max(3, Math.floor(height / 70));
      setPageSize((prev) => (prev !== newSize ? newSize : prev));
    };

    if (el.clientHeight > 0) {
      measureAndSet(el.clientHeight);
    }

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        measureAndSet(entry.contentRect.height);
      }
    });

    observer.observe(el);
    return () => observer.disconnect();
  }, [displayableTrades.length]);

  if (!displayableTrades.length) {
    return (
      <div className="card px-4 py-8 text-center flex-1">
        <p className="font-mono text-[13px] text-dim italic">No trades logged yet.</p>
      </div>
    );
  }

  const totalPages = Math.max(1, Math.ceil(displayableTrades.length / pageSize));
  const currentPage = Math.min(page, totalPages - 1);
  const visibleTrades = displayableTrades.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize
  );

  return (
    <div className="flex flex-col flex-1 h-full min-h-0 gap-3">
      <div ref={containerRef} className="card flex-1 overflow-y-auto min-h-0 divide-y divide-border">
        <AnimatePresence initial={false}>
          {visibleTrades.map((trade) => (
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
      
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-1">
          <button 
            disabled={currentPage === 0} 
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            className="font-mono text-[13px] text-ink disabled:text-dim hover:text-primary transition-colors"
          >
            ← PREV
          </button>
          <span className="font-mono text-[12px] text-dim">
            PAGE {currentPage + 1} OF {totalPages}
          </span>
          <button 
            disabled={currentPage >= totalPages - 1} 
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            className="font-mono text-[13px] text-ink disabled:text-dim hover:text-primary transition-colors"
          >
            NEXT →
          </button>
        </div>
      )}
    </div>
  );
}
