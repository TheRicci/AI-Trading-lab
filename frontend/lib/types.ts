export type LlmProvider = "gemini" | "github_models" | "groq" | string;

export interface Bot {
  botName: string;
  cash: number;
  holdings: Record<string, number>;
  totalValue?: number;
  strategyPrompt: string;
  llmProvider: LlmProvider;
  llmModel: string;
}

export type TradeAction = "buy" | "sell" | "hold";

// Bot LLM decision fields (not stored in Trade record, but shown via derived logic)
// position: "long" | "short" | "flat" -- the bot's intent
// leverage: 1.0–3.0 -- multiplier applied to portfolio value
// shares can be NEGATIVE when the bot is short a position
// cash can be NEGATIVE when the bot is using leverage
export interface Trade {
  id: string;
  botName: string;
  llmProvider: LlmProvider;
  ticker: string;
  action: TradeAction;    // "buy" | "sell" | "hold" (derived from position change)
  shares: number;         // absolute shares traded; check holdings for net direction
  price: number;
  reasoning: string;
  trigger: string;        // "session-open" | "session-close"
  timestamp: string;
}

export interface PortfolioHistoryPoint {
  timestamp: string;
  totalValue: number;
}

export type PortfolioHistoryByBot = Record<string, PortfolioHistoryPoint[]>;

export type AnalysisDirection = "long" | "short" | "watch" | "avoid" | string;

export interface AnalysisIdea {
  ticker_or_asset: string;
  thesis: string;            // 1-2 sentence executive summary
  detailed_report?: string;  // full markdown investment case (~150-250 words)
  suggested_direction: AnalysisDirection;
  price_context?: string;
}

export interface AnalysisReport {
  reportDate: string;
  overview: string;
  ideas: AnalysisIdea[];
  generatedAt?: string;
  notFound?: boolean;
}

export interface LiveTradeMessage extends Trade {
  type: "trade";
  totalValue: number;
}

export const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Gemini",
  groq: "Qwen / Groq",
};

export const PROVIDER_HEX: Record<string, string> = {
  gemini: "#818cf8",
  groq: "#fb923c",
};

export const STRATEGY_LABELS: Record<string, string> = {
  momentum: "Momentum",
  mean_reversion: "Mean Rev.",
  event_driven: "Event",
};

/** Extract strategy family from bot name like "momentum_gemini" */
export function strategyFromBotName(name: string): string {
  if (name.startsWith("momentum")) return "Momentum";
  if (name.startsWith("mean_reversion")) return "Mean Rev.";
  if (name.startsWith("event_driven")) return "Event";
  return name;
}
