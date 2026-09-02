import Link from "next/link";
import { getAnalysis, getAnalysisDates, getAnalysisOutcomes } from "@/lib/api";
import { IdeaCard } from "@/components/IdeaCard";

export const dynamic = "force-dynamic";

const formatDate = (date: string, year = false) => new Date(`${date}T12:00:00`).toLocaleDateString(undefined, year ? { month: "short", day: "numeric", year: "numeric" } : { month: "short", day: "numeric" });

export default async function AnalysisPage({ searchParams }: { searchParams: Promise<{ date?: string, model?: string }> }) {
  const { date: requestedDate, model: requestedModel } = await searchParams;
  try {
    const dates = await getAnalysisDates();
    if (!dates.length) return <div className="card p-8"><p className="font-mono text-[11px] text-dim">No analysis generated yet — check back after the morning run.</p></div>;
    const selectedDate = requestedDate && dates.includes(requestedDate) ? requestedDate : dates[0];
    const [reportsDict, outcomes] = await Promise.all([getAnalysis(selectedDate), getAnalysisOutcomes(selectedDate)]);
    
    // Determine available models for this date
    const availableProviders = Object.keys(reportsDict);
    if (!availableProviders.length) return <div className="card p-8"><p className="font-mono text-[11px] text-dim">No reports found for this date.</p></div>;

    const selectedProvider = requestedModel && availableProviders.includes(requestedModel) ? requestedModel : availableProviders[0];
    const report = reportsDict[selectedProvider];
    
    const outcomeByTicker = new Map(outcomes.map((outcome) => [outcome.ticker_or_asset.toUpperCase(), outcome]));
    const counts = report.ideas.reduce<Record<string, number>>((all, idea) => { const direction = idea.suggested_direction?.toLowerCase() ?? "other"; all[direction] = (all[direction] ?? 0) + 1; return all; }, {});

    return <div>
      <section className="analysis-hero mb-7">
        <div><div className="overline mb-2">Research archive</div><h1 className="analysis-title">Daily pick of the day</h1><p className="mt-2 max-w-[620px] text-[13px] leading-relaxed text-secondary">Each day the model picks the single most important setup across markets, writes a deep report on it, then the scorecard follows that asset afterwards — so you can judge the call against the market, not the narrative.</p></div>
        <div className="analysis-date-mark"><span>Viewing report</span><strong>{formatDate(selectedDate, true)}</strong></div>
      </section>
      
      <nav className="report-archive mb-4" aria-label="Analysis report archive"><div className="report-archive-label">Browse reports</div><div className="report-date-list">{dates.map((date, index) => <Link key={date} href={`/analysis?date=${date}&model=${selectedProvider}`} className={`report-date ${date === selectedDate ? "report-date-active" : ""}`}><span>{index === 0 ? "Latest · " : ""}{formatDate(date)}</span><small>{new Date(`${date}T12:00:00`).getFullYear()}</small></Link>)}</div></nav>
      
      {availableProviders.length > 1 && (
        <div className="mb-8 flex gap-2 border-b border-border pb-1">
          {availableProviders.map(provider => (
            <Link key={provider} href={`/analysis?date=${selectedDate}&model=${provider}`} className={`px-4 py-2 text-sm font-medium border-b-2 ${provider === selectedProvider ? "border-indigo-400 text-indigo-400" : "border-transparent text-secondary hover:text-white"}`}>
              {provider === "gemini" ? "Gemini 3.6 Flash" : provider === "groq" ? "GPT-OSS 120B" : provider}
            </Link>
          ))}
        </div>
      )}

      <div className="card report-summary mb-8 p-5"><div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between"><div><div className="section-head mb-2"><span className="overline">Today's market overview</span></div><p className="max-w-[720px] text-[14px] leading-relaxed text-secondary">{report.overview}</p><div className="mt-3 font-mono text-[10px] text-dim">Published <span className="text-secondary">{formatDate(report.reportDate, true)}</span></div></div><div className="flex gap-4 font-mono text-[10px]">{[["long", "Long", "text-up"], ["short", "Short", "text-down"], ["watch", "Watch", "text-amber"], ["avoid", "Avoid", "text-dim"]].map(([key, label, color]) => counts[key] ? <div key={key} className="text-center"><div className={`num text-[18px] font-semibold ${color}`}>{counts[key]}</div><div className="text-dim uppercase tracking-widest">{label}</div></div> : null)}</div></div></div>
      <div className="mb-4 flex items-end justify-between gap-4"><div className="section-head mb-0"><span className="overline">Pick of the day — deep dive</span></div><span className="font-mono text-[10px] text-dim">{report.ideas.length === 1 ? "1 pick" : `${report.ideas.length} picks`}</span></div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">{report.ideas.map((idea, index) => <IdeaCard key={`${idea.ticker_or_asset}-${index}`} idea={idea} outcome={outcomeByTicker.get(idea.ticker_or_asset.toUpperCase())} />)}</div>
      <div className="mt-8 border-t border-border pt-6"><p className="max-w-[680px] font-mono text-[10px] leading-relaxed text-dim">AI-generated hypotheses for a sandbox project. Outcome tracking shows market movement, not a complete measure of an idea’s validity. Not financial advice.</p></div>
    </div>;
  } catch (err) {
    return <div className="card p-8"><p className="font-mono text-[11px] text-dim">{err instanceof Error ? err.message : "Couldn't load the report archive."}</p></div>;
  }
}