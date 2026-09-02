import type { AnalysisOutcome, AnalysisReport, Bot, PortfolioHistoryByBot, Trade } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
// A checkout should be previewable immediately. Set NEXT_PUBLIC_MOCK=false
// alongside NEXT_PUBLIC_API_BASE to force a live Azure connection.
export const IS_MOCK = process.env.NEXT_PUBLIC_MOCK === "true" ||
  (process.env.NEXT_PUBLIC_MOCK !== "false" && !API_BASE);

// Mock loader works on both server and client.
async function loadMock<T>(file: string): Promise<T> {
  if (typeof window === "undefined") {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const raw = readFileSync(join(process.cwd(), "public", "mock", file), "utf-8");
    return JSON.parse(raw) as T;
  }
  const res = await fetch(`/mock/${file}`);
  if (!res.ok) throw new Error(`Mock ${file} not found (${res.status})`);
  return res.json() as Promise<T>;
}

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_BASE) throw new Error("NEXT_PUBLIC_API_BASE not set — configure the Azure API or enable mock mode.");
  const res = await fetch(`${API_BASE}${path}`, init);
  const body = await res.text();

  if (!res.ok) throw new ApiError(`${path} → ${res.status}`, res.status);
  if (!body.trim()) throw new Error(`${path} returned an empty response.`);

  try {
    return JSON.parse(body) as T;
  } catch {
    throw new Error(`${path} returned invalid JSON.`);
  }
}

export function getLeaderboard(): Promise<Bot[]> {
  return IS_MOCK ? loadMock<Bot[]>("leaderboard.json") : getJson<Bot[]>("/api/leaderboard");
}

export function getTrades(bot?: string): Promise<Trade[]> {
  if (IS_MOCK) {
    return loadMock<Trade[]>("trades.json").then((t) => bot ? t.filter((x) => x.botName === bot) : t);
  }
  const qs = bot ? `?bot=${encodeURIComponent(bot)}` : "";
  return getJson<Trade[]>(`/api/trades${qs}`);
}

export function getPortfolioHistory(): Promise<PortfolioHistoryByBot> {
  return IS_MOCK ? loadMock<PortfolioHistoryByBot>("portfolio-history.json") : getJson<PortfolioHistoryByBot>("/api/portfolio-history");
}

export function getAnalysis(date?: string): Promise<Record<string, AnalysisReport>> {
  if (IS_MOCK) {
    return loadMock<Record<string, Record<string, AnalysisReport>>>("analysis-history.json").then((reports) => {
      const dates = Object.keys(reports).sort().reverse();
      return reports[date && reports[date] ? date : dates[0]];
    });
  }
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return getJson<Record<string, AnalysisReport>>(`/api/analysis${qs}`, { next: { revalidate: 300 } } as RequestInit);
}

export async function getAnalysisDates(): Promise<string[]> {
  if (IS_MOCK) return loadMock<Record<string, Record<string, AnalysisReport>>>("analysis-history.json").then((reports) => Object.keys(reports).sort().reverse());
  try {
    return await getJson<string[]>("/api/analysis/dates", { next: { revalidate: 300 } } as RequestInit);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;

    // Backwards compatibility: the original public API exposed only
    // /analysis. It can still power a one-report archive until the API
    // Function is deployed with the optional /analysis/dates route.
    try {
      const report = await getJson<AnalysisReport>("/api/analysis", { next: { revalidate: 300 } } as RequestInit);
      return report.notFound ? [] : [report.reportDate];
    } catch (fallbackError) {
      if (fallbackError instanceof ApiError && fallbackError.status === 404) return [];
      throw fallbackError;
    }
  }
}

export function getAnalysisOutcomes(date: string): Promise<AnalysisOutcome[]> {
  if (IS_MOCK) {
    return loadMock<Record<string, AnalysisOutcome[]>>("analysis-outcomes.json").then((outcomes) => outcomes[date] ?? []);
  }
  return getJson<AnalysisOutcome[]>(`/api/analysis/outcomes?date=${encodeURIComponent(date)}`, { next: { revalidate: 300 } } as RequestInit);
}

export async function getNegotiateUrl(): Promise<string> {
  if (IS_MOCK) return "";
  const { url } = await getJson<{ url: string }>("/api/negotiate");
  return url;
}


