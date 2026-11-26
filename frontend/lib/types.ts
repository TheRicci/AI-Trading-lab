export type LlmProvider = "gemini" | "github_models" | "groq" | "groq_tavily" | string;

export interface Bot {
  botName: string;
  cash: number;                  // raw signed cash (may be negative under leverage)
  availableMargin: number;       // max(0, cash) — undeployed equity
  marginUsed: number;            // abs(min(0, cash)) — equity posted as margin
  positions: Record<string, Position>;  // rich per-position data with live P&L
  totalValue?: number;
  startingCash: number;          // baseline for P&L calculations
  strategyPrompt: string;
  llmProvider: LlmProvider;
  llmModel: string;
}

export type AssetType = "stock" | "etf" | "crypto" | "forex" | "brazil" | string;

export interface Position {
  direction: "long" | "short";
  shares: number;                // signed (positive = long, negative = short)
  entryPrice: number;
  leverage: number;
  notional: number;              // abs(shares) × entryPrice
  marginUsed: number;            // notional / leverage
  assetType?: AssetType;         // declared by LLM at open time
  currentPrice?: number;         // enriched by API at read time
  unrealizedPnl?: number;        // direction × notional × (current/entry − 1)
  unrealizedPct?: number;        // unrealizedPnl as % of startingCash
}

export type TradeAction   = "buy" | "sell" | "hold";
export type TradePosition = "long" | "short" | "flat" | "none";

export interface Trade {
  id: string;
  botName: string;
  llmProvider: LlmProvider;
  llmModel?: string;
  ticker: string;
  assetType?: AssetType;         // declared by LLM: "stock" | "etf" | "crypto" | "forex" | "brazil"
  action: TradeAction;
  position: TradePosition;       // bot's intent (long/short/flat/none)
  leverage: number;              // 1.0–10.0
  shares: number;                // unsigned delta traded
  price: number;
  reasoning: string;
  trigger: string;               // "session-open" | "session-close"
  timestamp: string;
  totalValue?: number;           // NAV after this trade
  // Close-specific: set when position === "flat" to describe what was closed
  closedDirection?: "long" | "short";
  closedLeverage?:  number;
  realizedPnl?:     number;
  partial?:         boolean;
}

export interface PortfolioHistoryPoint {
  timestamp: string;
  totalValue: number;
}

export type PortfolioHistoryByBot = Record<string, PortfolioHistoryPoint[]>;

export type AnalysisDirection = "long" | "short" | "watch" | "avoid" | string;

export interface AnalysisIdea {
  ticker_or_asset: string;
  company_or_asset_name?: string;  // full company/asset name (e.g. "Nvidia Corp")
  thesis: string;            // 1-2 sentence executive summary
  detailed_report?: string;  // full markdown investment case (deep ~400-700 words for single pick)
  suggested_direction: AnalysisDirection;
  price_context?: string;
  price_at_analysis?: number;
}

export interface AnalysisOutcomePoint {
  date: string;
  price: number;
}

export interface AnalysisOutcome {
  ticker_or_asset: string;
  initialPrice?: number;
  currentPrice?: number;
  returnPct?: number;
  history: AnalysisOutcomePoint[];
  asOf?: string;
}

export interface AnalysisReport {
  id: string;
  reportDate: string;
  model?: string;
  provider?: string;
  overview: string;
  ideas: AnalysisIdea[];
  generatedAt: string;
  notFound?: boolean; // backwards compat for ungenerated days
}

export const BOT_COLORS = [
  "#8b8dff", // indigo
  "#2dd4bf", // teal
  "#f5a524", // gold
  "#f472b6", // rose
  "#60a5fa", // blue
  "#a3e635", // lime
];

export const BOT_SPECIFIC_COLORS: Record<string, string> = {
  open_gemini:  "#818cf8", // indigo
  open_gptoss:  "#a855f7", // purple
  open_qwen:    "#38bdf8", // sky blue
  close_gemini: "#2dd4bf", // teal
  close_gptoss: "#f59e0b", // amber
  close_qwen:   "#f43f5e", // rose/coral
};

/** A stable color per bot, independent of its model provider. */
export function botColorFor(name: string): string {
  if (BOT_SPECIFIC_COLORS[name]) return BOT_SPECIFIC_COLORS[name];
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return BOT_COLORS[hash % BOT_COLORS.length];
}

/** Format clean display name for both open and close bots. */
export function botDisplayName(name: string): string {
  if (name === "open_gemini")  return "Open Gemini";
  if (name === "open_gptoss")  return "Open GPT-OSS";
  if (name === "open_qwen")    return "Open Qwen";
  if (name === "close_gemini") return "Close Gemini";
  if (name === "close_gptoss") return "Close GPT-OSS";
  if (name === "close_qwen")   return "Close Qwen";
  const parts = name.split("_");
  if (parts.length === 2) {
    const session = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
    const model = parts[1].toUpperCase() === "GPTOSS" ? "GPT-OSS" : parts[1].charAt(0).toUpperCase() + parts[1].slice(1);
    return `${session} ${model}`;
  }
  return name.replace(/_/g, " ");
}

export interface LiveTradeMessage extends Trade {
  type: "trade";
  totalValue: number;
}

export const PROVIDER_LABELS: Record<string, string> = {
  gemini:      "Gemini",
  groq:        "Groq",
  groq_tavily: "Groq + Tavily",
};

export const PROVIDER_HEX: Record<string, string> = {
  gemini:      "#818cf8",
  groq:        "#fb923c",
  groq_tavily: "#38bdf8",
};

export const MODEL_LABELS: Record<string, string> = {
  "gemini-3.6-flash":            "Gemini 3.6 Flash",
  "gemini-2.5-pro":              "Gemini 3.6 Flash",
  "gemini-2.5-flash":            "Gemini 2.5 Flash",
  "gemini-3.1-flash-lite":       "Gemini 3.1 Flash-Lite",
  "openai/gpt-oss-120b":         "GPT-OSS 120B (Groq + Tavily)",
  "qwen/qwen3.8-27b":            "Qwen 3.8 27B (Groq + Tavily)",
  "qwen/qwen3.6-27b":            "Qwen 3.6 27B (Groq + Tavily)",
  "llama-3.3-70b-versatile":     "Llama 3.3 70B (Groq + Tavily)",
  "groq/compound-mini":          "Groq Compound Mini",
  "groq/compound":               "Groq Compound",
};

/** Short model name without the provider suffix — for the card headline. */
export const MODEL_SHORT: Record<string, string> = {
  "gemini-3.6-flash":        "Gemini 3.6 Flash",
  "gemini-2.5-pro":          "Gemini 3.6 Flash",
  "gemini-2.5-flash":        "Gemini 2.5 Flash",
  "gemini-3.1-flash-lite":   "Gemini 3.1 Flash-Lite",
  "openai/gpt-oss-120b":     "GPT-OSS 120B",
  "qwen/qwen3.8-27b":        "Qwen 3.8 27B",
  "qwen/qwen3.6-27b":        "Qwen 3.6 27B",
  "llama-3.3-70b-versatile": "Llama 3.3 70B",
  "groq/compound-mini":      "Groq Compound Mini",
  "groq/compound":           "Groq Compound",
};

/** Provider sub-label shown below the model name, e.g. "Groq + Tavily". */
export const MODEL_PROVIDER_LABEL: Record<string, string> = {
  "gemini-3.6-flash":        "Google Gemini",
  "gemini-2.5-pro":          "Google Gemini",
  "gemini-2.5-flash":        "Google Gemini",
  "gemini-3.1-flash-lite":   "Google Gemini",
  "openai/gpt-oss-120b":     "Groq + Tavily",
  "qwen/qwen3.8-27b":        "Groq + Tavily",
  "qwen/qwen3.6-27b":        "Groq + Tavily",
  "llama-3.3-70b-versatile": "Groq + Tavily",
  "groq/compound-mini":      "Groq",
  "groq/compound":           "Groq",
};

export const MODEL_HEX: Record<string, string> = {
  "gemini-3.6-flash":            "#818cf8",
  "gemini-2.5-flash":            "#818cf8",
  "gemini-3.1-flash-lite":       "#818cf8",
  "openai/gpt-oss-120b":         "#38bdf8",
  "qwen/qwen3.8-27b":            "#34d399",
  "qwen/qwen3.6-27b":            "#34d399",
  "llama-3.3-70b-versatile":     "#a78bfa",
  "groq/compound-mini":          "#fb923c",
  "groq/compound":               "#fb923c",
};

export const STRATEGY_LABELS: Record<string, string> = {
  open: "Open · General",
  close_gemini: "Close · Catalyst Drift",
  close_gptoss: "Close · Macro Rotation",
  close_qwen: "Close · Panic Reversal",
};

/** Formats human-readable strategy badge for open and close bots. */
export function strategyFromBotName(name: string): string {
  if (name === "close_gemini") return "Close · Catalyst Drift";
  if (name === "close_gptoss") return "Close · Macro Rotation";
  if (name === "close_qwen")   return "Close · Panic Reversal";
  if (name.startsWith("close_")) return "Close · Strategy";
  if (name.startsWith("open_"))  return "Open · General";
  return "General";
}
