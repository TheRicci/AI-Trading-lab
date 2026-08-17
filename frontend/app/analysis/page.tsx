import { getAnalysis } from "@/lib/api";
import { IdeaCard } from "@/components/IdeaCard";

export const dynamic = "force-dynamic";

export default async function AnalysisPage() {
  let report;

  try {
    report = await getAnalysis();
  } catch (err) {
    return (
      <div className="card p-8">
        <div className="section-head mb-4"><span className="overline">Daily Analysis</span></div>
        <p className="font-mono text-[11px] text-dim">
          {err instanceof Error ? err.message : "Couldn't load today's analysis."}
        </p>
      </div>
    );
  }

  if (report.notFound || !report.ideas?.length) {
    return (
      <div className="card p-8">
        <div className="section-head mb-4"><span className="overline">Daily Analysis</span></div>
        <p className="font-mono text-[11px] text-dim italic">
          No analysis generated yet — check back after the morning run.
        </p>
      </div>
    );
  }

  // Count directions
  const counts = report.ideas.reduce<Record<string, number>>((acc, idea) => {
    const d = idea.suggested_direction?.toLowerCase() ?? "other";
    acc[d] = (acc[d] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>

      {/* ── Report header ──────────────────────────────────────────────────── */}
      <div className="card mb-8 p-5">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">

          {/* Left: description + meta */}
          <div>
            <div className="section-head mb-2">
              <span className="overline">Daily Analysis</span>
            </div>
            <p className="text-[13px] text-dim max-w-[520px] leading-relaxed mb-3">
              Scanning any market, worldwide, for{" "}
              <strong className="text-secondary font-medium">asymmetries</strong>{" "}
              — situations where the market&rsquo;s reaction to news appears disproportionate
              to its actual significance, creating mispricings on either side.
            </p>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-[10px] text-dim">
              <span>Date: <span className="text-secondary">{report.reportDate}</span></span>
              {report.generatedAt && (
                <span>
                  Generated:{" "}
                  <span className="text-secondary">
                    {new Date(report.generatedAt).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
                  </span>
                </span>
              )}
              <span>Ideas: <span className="text-secondary">{report.ideas.length}</span></span>
            </div>
          </div>

          {/* Right: direction tally */}
          <div className="flex gap-4 font-mono text-[10px] flex-shrink-0">
            {[
              { key: "long",  label: "Long",  color: "text-up"    },
              { key: "short", label: "Short", color: "text-down"  },
              { key: "watch", label: "Watch", color: "text-amber" },
              { key: "avoid", label: "Avoid", color: "text-dim"   },
            ].map(({ key, label, color }) =>
              counts[key] ? (
                <div key={key} className="text-center">
                  <div className={`num text-[18px] font-semibold ${color}`}>{counts[key]}</div>
                  <div className="text-dim uppercase tracking-widest">{label}</div>
                </div>
              ) : null
            )}
          </div>
        </div>
      </div>

      {/* ── Market overview ────────────────────────────────────────────────── */}
      <div className="mb-8">
        <div className="section-head mb-4">
          <span className="overline">Market Overview</span>
        </div>
        <div className="overview-block">
          <p className="text-[14px] text-secondary leading-relaxed max-w-[780px]">
            {report.overview}
          </p>
        </div>
      </div>

      {/* ── Ideas ─────────────────────────────────────────────────────────── */}
      <div>
        <div className="section-head mb-4">
          <span className="overline">Asymmetries identified · {report.ideas.length}</span>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-0 xl:gap-4">
          {report.ideas.map((idea, i) => (
            <IdeaCard key={`${idea.ticker_or_asset}-${i}`} idea={idea} />
          ))}
        </div>
      </div>

      {/* ── Disclaimer ────────────────────────────────────────────────────── */}
      <div className="mt-8 pt-6 border-t border-border">
        <p className="font-mono text-[10px] text-dim leading-relaxed max-w-[640px]">
          AI-generated hypotheses for a sandbox project. Not financial advice. Ideas are
          speculative and should not be the basis for actual investment decisions.
        </p>
      </div>
    </div>
  );
}
