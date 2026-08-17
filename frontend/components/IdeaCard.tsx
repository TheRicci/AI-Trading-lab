"use client";

import { useState } from "react";
import type { AnalysisIdea } from "@/lib/types";

const DIR: Record<string, {
  strip: string; badge: string; icon: string; label: string;
}> = {
  long:  { strip: "idea-long",  badge: "text-up   bg-up-dim",    icon: "▲", label: "LONG"  },
  short: { strip: "idea-short", badge: "text-down bg-down-dim",  icon: "▼", label: "SHORT" },
  watch: { strip: "idea-watch", badge: "text-amber bg-amber-dim", icon: "◎", label: "WATCH" },
  avoid: { strip: "idea-avoid", badge: "text-dim  bg-surface-hi", icon: "—", label: "AVOID" },
};
const FALLBACK = { strip: "idea-avoid", badge: "text-dim bg-surface-hi", icon: "•", label: "NEUTRAL" };

export function IdeaCard({ idea }: { idea: AnalysisIdea }) {
  const [expanded, setExpanded] = useState(false);
  const dir = (idea.suggested_direction ?? "").toLowerCase();
  const d   = DIR[dir] ?? FALLBACK;
  const hasReport = !!idea.detailed_report;

  return (
    <div className={`card mb-3 pl-5 pr-5 py-4 ${d.strip} transition-colors duration-150`}>
      {/* Header: ticker + direction badge */}
      <div className="flex items-center gap-3 mb-3">
        <span className="num text-[16px] font-semibold text-ink">{idea.ticker_or_asset}</span>
        <span className={`inline-flex items-center gap-1 font-mono text-[9px] tracking-[0.1em] uppercase px-2 py-0.5 rounded ${d.badge}`}>
          <span>{d.icon}</span>{d.label}
        </span>
      </div>

      {/* Executive thesis */}
      <p className="text-[13px] text-secondary leading-relaxed mb-3">{idea.thesis}</p>

      {/* Detailed report — expand/collapse */}
      {hasReport && (
        <>
          {expanded && (
            <div className="border-t border-border pt-3 mt-1 mb-3">
              <div className="font-mono text-[9px] tracking-[0.12em] uppercase text-dim mb-3">
                Full Report
              </div>
              {/* Render markdown sections as plain text blocks — clean and readable */}
              <div className="space-y-2">
                {idea.detailed_report!
                  .split(/\n(?=##\s)/)
                  .map((section, i) => {
                    const lines = section.split("\n").filter(Boolean);
                    const heading = lines[0]?.replace(/^##\s+/, "");
                    const body = lines.slice(1).join(" ").trim();
                    return (
                      <div key={i} className="mb-3">
                        {heading && (
                          <div className="font-mono text-[10px] font-semibold text-secondary uppercase tracking-wider mb-1">
                            {heading}
                          </div>
                        )}
                        {body && (
                          <p className="text-[12px] text-dim leading-relaxed">{body}</p>
                        )}
                      </div>
                    );
                  })}
              </div>
            </div>
          )}
          <button
            onClick={() => setExpanded((v) => !v)}
            className="font-mono text-[10px] tracking-[0.08em] uppercase text-dim hover:text-secondary transition-colors duration-100 mt-1"
          >
            {expanded ? "▲ Collapse" : "▼ Full report"}
          </button>
        </>
      )}

      {/* Price context */}
      {idea.price_context && (
        <div className={`font-mono text-[11px] text-dim border-t border-border pt-2 mt-3`}>
          {idea.price_context}
        </div>
      )}
    </div>
  );
}
