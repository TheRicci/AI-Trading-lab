import type { AnalysisReport, Bot, PortfolioHistoryByBot, Trade } from "./types";

const IS_MOCK = process.env.NEXT_PUBLIC_MOCK === "true";
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

// ─── Mock loader — works on both server and client ───────────────────────────

async function loadMock<T>(file: string): Promise<T> {
  // Server components can't use relative URLs — use fs instead
  if (typeof window === "undefined") {
    const { readFileSync } = await import("fs");
    const { join } = await import("path");
    const raw = readFileSync(join(process.cwd(), "public", "mock", file), "utf-8");
    return JSON.parse(raw) as T;
  }
  // Client components fetch normally
  const res = await fetch(`/mock/${file}`);
  if (!res.ok) throw new Error(`Mock ${file} not found (${res.status})`);
  return res.json() as Promise<T>;
}

// ─── Real API helper ──────────────────────────────────────────────────────────

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  if (!API_BASE) throw new Error("NEXT_PUBLIC_API_BASE not set — use NEXT_PUBLIC_MOCK=true for local dev.");
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok && res.status !== 404) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// ─── Exports ──────────────────────────────────────────────────────────────────

export function getLeaderboard(): Promise<Bot[]> {
  if (IS_MOCK) return loadMock<Bot[]>("leaderboard.json");
  return getJson<Bot[]>("/api/leaderboard");
}

export function getTrades(bot?: string): Promise<Trade[]> {
  if (IS_MOCK) {
    return loadMock<Trade[]>("trades.json").then((t) =>
      bot ? t.filter((x) => x.botName === bot) : t
    );
  }
  const qs = bot ? `?bot=${encodeURIComponent(bot)}` : "";
  return getJson<Trade[]>(`/api/trades${qs}`);
}

export function getPortfolioHistory(): Promise<PortfolioHistoryByBot> {
  if (IS_MOCK) return loadMock<PortfolioHistoryByBot>("portfolio-history.json");
  return getJson<PortfolioHistoryByBot>("/api/portfolio-history");
}

export function getAnalysis(date?: string): Promise<AnalysisReport> {
  if (IS_MOCK) return loadMock<AnalysisReport>("analysis.json");
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  return getJson<AnalysisReport>(`/api/analysis${qs}`, {
    next: { revalidate: 300 },
  } as RequestInit);
}

export async function getNegotiateUrl(): Promise<string> {
  if (IS_MOCK) return "";
  const { url } = await getJson<{ url: string }>("/api/negotiate");
  return url;
}
