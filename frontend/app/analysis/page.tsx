import Link from "next/link";
import { getAnalysis, getAnalysisDates, getAnalysisOutcomes } from "@/lib/api";
import { IdeaCard } from "@/components/IdeaCard";

export const dynamic = "force-dynamic";

const formatDate = (date: string, year = false) =>
  new Date(`${date}T12:00:00`).toLocaleDateString(undefined, year
    ? { month: "short", day: "numeric", year: "numeric" }
    : { month: "short", day: "numeric" });

export default async function AnalysisPage({ searchParams }: { searchParams: Promise<{ date?: string }> }) {
  const { date: requestedDate } = await searchParams;
  try {
    const dates = await getAnalysisDates();
    if (!dates.length) return (
      <div className="card p-8">
        <p className="font-mono text-[11px] text-dim">No analysis generated yet — check back after the morning run.</p>
      </div>
    );

    const selectedDate = requestedDate && dates.includes(requestedDate) ? requestedDate : dates[0];
    const [reportsDict, outcomes] = await Promise.all([getAnalysis(selectedDate), getAnalysisOutcomes(selectedDate)]);

    const providers = Object.keys(reportsDict);
    if (!providers.length) return (
      <div className="card p-8">
        <p className="font-mono text-[11px] text-dim">No reports found for this date.</p>
      </div>
    );

    // One report per day — always use the first (only) provider
    const report = reportsDict[providers[0]];
    const outcomeByTicker = new Map(outcomes.map((o) => [o.ticker_or_asset.toUpperCase(), o]));

    return (
      <div className="max-w-4xl mx-auto">
        {/* ── Hero ───────────────────────────────────────────────────────── */}
        <section className="analysis-hero mb-7">
          <div>
            <div className="overline mb-2">Research archive</div>
            <h1 className="analysis-title">Pick of the day</h1>
            <p className="mt-2 max-w-[560px] text-[13px] leading-relaxed text-secondary">
              Each day the model identifies the single most compelling market setup, writes a deep-dive on it, then the scorecard tracks that asset — so you can judge the call against the market, not the narrative.
            </p>
          </div>
          <div className="analysis-date-mark">
            <span>Viewing report</span>
            <strong>{formatDate(selectedDate, true)}</strong>
          </div>
        </section>

        {/* ── Date archive ───────────────────────────────────────────────── */}
        <nav className="report-archive mb-6" aria-label="Analysis report archive">
          <div className="report-archive-label">Archive</div>
          <div className="report-date-list">
            {dates.map((date, index) => (
              <Link key={date} href={`/analysis?date=${date}`} className={`report-date ${date === selectedDate ? "report-date-active" : ""}`}>
                <span>{index === 0 ? "Latest · " : ""}{formatDate(date)}</span>
                <small>{new Date(`${date}T12:00:00`).getFullYear()}</small>
              </Link>
            ))}
          </div>
        </nav>

        {/* ── Market overview ────────────────────────────────────────────── */}
        <div className="card report-summary mb-6 px-6 py-5">
          <div className="overline mb-3">Today's market context</div>
          <p className="text-[14px] leading-relaxed text-secondary max-w-[760px]">{report.overview}</p>
          <div className="mt-3 font-mono text-[10px] text-dim">
            Published <span className="text-secondary">{formatDate(report.reportDate, true)}</span>
          </div>
        </div>

        {/* ── Pick ───────────────────────────────────────────────────────── */}
        <div className="mb-4 flex items-center justify-between">
          <div className="section-head mb-0"><span className="overline">Deep dive</span></div>
          <span className="font-mono text-[10px] text-dim">{report.ideas.length === 1 ? "1 pick today" : `${report.ideas.length} picks`}</span>
        </div>
        <div className="space-y-6">
          {report.ideas.map((idea, index) => (
            <IdeaCard
              key={`${idea.ticker_or_asset}-${index}`}
              idea={idea}
              outcome={outcomeByTicker.get(idea.ticker_or_asset.toUpperCase())}
            />
          ))}
        </div>

        {/* ── Disclaimer ─────────────────────────────────────────────────── */}
        <div className="mt-10 border-t border-border pt-6">
          <p className="max-w-[680px] font-mono text-[10px] leading-relaxed text-dim">
            AI-generated hypotheses for a sandbox project. Outcome tracking shows market movement, not a complete measure of an idea&apos;s validity. Not financial advice.
          </p>
        </div>
      </div>
    );
  } catch (err) {
    return (
      <div className="card p-8">
        <p className="font-mono text-[11px] text-dim">{err instanceof Error ? err.message : "Couldn't load the report archive."}</p>
      </div>
    );
  }
}