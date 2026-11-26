"use client";

import { OutcomeTrend } from "@/components/OutcomeTrend";
import type { AnalysisIdea, AnalysisOutcome } from "@/lib/types";

const DIR: Record<string, { strip: string; badge: string; icon: string; label: string }> = {
  long:  { strip: "idea-long",  badge: "text-up   bg-up-dim",    icon: "▲", label: "LONG"  },
  short: { strip: "idea-short", badge: "text-down bg-down-dim",  icon: "▼", label: "SHORT" },
  watch: { strip: "idea-watch", badge: "text-amber bg-amber-dim", icon: "◎", label: "WATCH" },
  avoid: { strip: "idea-avoid", badge: "text-dim  bg-surface-hi", icon: "—", label: "AVOID" },
};
const FALLBACK = { strip: "idea-avoid", badge: "text-dim bg-surface-hi", icon: "•", label: "NEUTRAL" };

function renderMarkdownSections(markdown: string) {
  return markdown
    .split(/\n(?=##\s)/)
    .map((section, i) => {
      const lines = section.split("\n").filter(Boolean);
      const heading = lines[0]?.replace(/^##\s+/, "");
      const body = lines.slice(1).join(" ").trim();
      return (
        <div key={i} className="mb-5">
          {heading && (
            <div className="font-mono text-[10px] font-semibold text-secondary uppercase tracking-wider mb-2">
              {heading}
            </div>
          )}
          {body && (
            <p className="text-[14px] text-secondary leading-relaxed">{body}</p>
          )}
        </div>
      );
    });
}

export function IdeaCard({ idea, outcome }: { idea: AnalysisIdea; outcome?: AnalysisOutcome }) {
  const dir = (idea.suggested_direction ?? "").toLowerCase();
  const d   = DIR[dir] ?? FALLBACK;

  return (
    <div className={`card ${d.strip} overflow-hidden`}>
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="px-7 pt-6 pb-5 border-b border-border">
        <div className="flex items-center gap-3 mb-4">
          <span className={`inline-flex items-center gap-1.5 font-mono text-[10px] tracking-[0.1em] uppercase px-2.5 py-1 rounded ${d.badge}`}>
            <span>{d.icon}</span>{d.label}
          </span>
          {idea.company_or_asset_name && idea.company_or_asset_name !== idea.ticker_or_asset && (
            <span className="font-mono text-[10px] text-dim uppercase tracking-widest">{idea.company_or_asset_name}</span>
          )}
        </div>
        <div className="flex items-end justify-between gap-4 flex-wrap">
          <div>
            <div className="num text-[3rem] font-semibold leading-none tracking-tight text-ink">
              {idea.ticker_or_asset}
            </div>
            {idea.price_at_analysis != null && (
              <div className="font-mono text-[11px] text-dim mt-1.5">
                At analysis · ${idea.price_at_analysis.toFixed(2)}
              </div>
            )}
          </div>
          {/* Price context tag */}
          {idea.price_context && (
            <div className="max-w-xs font-mono text-[11px] text-dim italic text-right leading-snug">
              {idea.price_context}
            </div>
          )}
        </div>
      </div>

      {/* ── Body ────────────────────────────────────────────────────────────── */}
      <div className="px-7 py-6 lg:grid lg:grid-cols-[1fr_320px] lg:gap-10">
        {/* Left — thesis + full report */}
        <div>
          {/* Thesis */}
          <p className="text-[16px] leading-relaxed text-ink mb-6">{idea.thesis}</p>

          {/* Full report sections */}
          {idea.detailed_report && (
            <div className="border-t border-border pt-5">
              <div className="overline mb-4">Full report</div>
              {renderMarkdownSections(idea.detailed_report)}
            </div>
          )}
        </div>

        {/* Right — outcome scorecard */}
        <div className="mt-6 lg:mt-0">
          <div className="overline mb-3">Market scorecard</div>
          <OutcomeTrend idea={idea} outcome={outcome} />
        </div>
      </div>
    </div>
  );
}
