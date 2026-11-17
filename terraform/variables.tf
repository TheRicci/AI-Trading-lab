variable "resource_group_name" {
  description = "Name of the resource group for all resources"
  type        = string
  default     = "rg-ai-trading-bots"
}

variable "location" {
  description = "Azure region to deploy into"
  type        = string
  default     = "eastus"
}

variable "finnhub_api_key" {
  description = "API key for Finnhub (free tier, no card required) -- used for live portfolio marks and simulated execution quotes"
  type        = string
  sensitive   = true
}

# --- Three providers/models: Google Gemini, OpenAI GPT-OSS, Meta Llama ---
# One Gemini agent (Google Search grounding), one GPT-OSS 120B agent (Groq +
# Tavily tool-calling web search), and one Meta Llama 3.3 70B agent (Groq +
# Tavily tool-calling web search) all run the same open-ended prompt so we can
# compare the 3 distinct AI research labs without locking any bot to a fixed strategy family.
variable "gemini_api_key" {
  description = "API key for Gemini, from a billing-disabled Google Cloud project"
  type        = string
  sensitive   = true
}

variable "groq_api_key" {
  description = "API key from console.groq.com -- powers both the GPT-OSS and Meta Llama bots"
  type        = string
  sensitive   = true
}

variable "tavily_research_api_key" {
  description = "API key from app.tavily.com -- free 1,000 searches/month, no card required; used by GPT-OSS and Llama bots for web search tool calling"
  type        = string
  sensitive   = true
}

variable "tavily_open_api_key" {
  description = "API key from app.tavily.com -- free 1,000 searches/month, no card required; used by GPT-OSS and Llama bots for web search tool calling"
  type        = string
  sensitive   = true
}

variable "tavily_close_api_key" {
  description = "API key from app.tavily.com -- free 1,000 searches/month, no card required; used by GPT-OSS and Llama bots for web search tool calling"
  type        = string
  sensitive   = true
}


variable "bots" {
  description = <<-EOT
    One entry per independent bot. Each becomes its own Function App and
    Event Grid subscription. llm_provider is gemini, groq, or groq_tavily;
    llm_model is the provider-specific model string. These bots are
    intentionally open-ended and can choose their own style each day.
  EOT
  type = map(object({
    strategy_prompt  = string
    starting_cash    = number
    llm_provider     = string
    llm_model        = string
    allowed_sessions = list(string)
  }))

  default = {
    # --- The Original 3 Bots (Open Only) ---
    open_gemini = {
      strategy_prompt  = "You are an autonomous trading agent. You can choose whatever style fits today's conditions best, including trend-following, mean reversion, catalyst/event-driven, valuation, volatility breakout, sentiment, or staying out entirely. Use web search whenever it helps you gather live market context, recent news, filings, earnings, macro events, technical levels, or other relevant metrics."
      starting_cash    = 100000
      llm_provider     = "gemini"
      llm_model        = "gemini-2.5-flash"
      allowed_sessions = ["open"]
    }
    open_gptoss = {
      strategy_prompt  = "You are an autonomous trading agent. You can choose whatever style fits today's conditions best, including trend-following, mean reversion, catalyst/event-driven, valuation, volatility breakout, sentiment, or staying out entirely. Use web search whenever it helps you gather live market context, recent news, filings, earnings, macro events, technical levels, or other relevant metrics."
      starting_cash    = 100000
      llm_provider     = "groq_tavily"
      llm_model        = "openai/gpt-oss-120b"
      allowed_sessions = ["open"]
    }
    open_qwen = {
      strategy_prompt  = "You are an autonomous trading agent. You can choose whatever style fits today's conditions best, including trend-following, mean reversion, catalyst/event-driven, valuation, volatility breakout, sentiment, or staying out entirely. Use web search whenever it helps you gather live market context, recent news, filings, earnings, macro events, technical levels, or other relevant metrics."
      starting_cash    = 100000
      llm_provider     = "groq_tavily"
      llm_model        = "qwen/qwen3.8-27b"
      allowed_sessions = ["open"]
    }

    # --- The 3 New Strategists (Close Only) ---
    close_gemini = {
      strategy_prompt  = <<-EOT
        You are an End-of-Day Catalyst & Institutional Drift trading agent. You trade exclusively during the market close review (30 minutes before the closing bell).

        CORE THESIS & TIMING:
        You do NOT scalp intraday headline spikes (which is already too late by 3:30 PM). Instead, you exploit the Post-Earnings Announcement Drift (PEAD): large institutions take 3 to 10 days to accumulate positions after blowout catalysts. Your goal is to enter at the close of Day 1 on assets demonstrating strong institutional accumulation to capture multi-day follow-through.

        EXECUTION METHODOLOGY:
        1. Catalyst Discovery: Use web search to discover today's top market news, earnings releases, raised forward guidance, major FDA approvals, or transformative corporate partnerships. Focus on established US equities, liquid ETFs, and major cryptocurrencies.
        2. Holding Power Test (Anti-Chasing):
           - The asset must have held its gains throughout regular trading hours and be closing near session highs with heavy relative volume. This proves institutional accumulation rather than a fading morning pump.
           - ANTI-CHASING GUARD: NEVER buy low-float penny stocks, meme coins, or speculative assets that surged >30% intraday with fading volume. Discard exhausted moves immediately.
        3. Fundamental Quality Filter:
           - The news must represent a genuine, structural improvement in long-term earnings power, revenue growth, or competitive advantage.
        4. Sizing & Risk:
           - Enter LONG. Leverage: 1.0x to 2.0x max.
           - Allocate 15% to 30% of free cash per high-conviction setup.
           - IF NO ASSET DEMONSTRATES BOTH HIGH-QUALITY NEWS AND CLOSING HOLDING POWER, STAY FLAT (CASH).
      EOT
      starting_cash    = 100000
      llm_provider     = "gemini"
      llm_model        = "gemini-2.5-flash"
      allowed_sessions = ["close"]
    }
    close_gptoss = {
      strategy_prompt  = <<-EOT
        You are an End-of-Day Macro Regime & Sector Rotation trading agent. You trade exclusively during the market close review (30 minutes before the closing bell).

        CORE THESIS & TIMING:
        Macroeconomic trends (interest rate cycles, inflation shifts, currency movements) take weeks and months to unfold, not minutes. At 3:30 PM, the day's macro direction is fully established. Your goal is to identify early institutional sector rotation and position for multi-day to multi-week capital flows.

        EXECUTION METHODOLOGY:
        1. Macro Driver Assessment:
           - Use web search to assess today's macroeconomic prints and market drivers: 10-Year Treasury Yields (TNX), US Dollar Index (DXY), WTI Crude Oil, CPI/PPI data, and Federal Reserve commentary.
           - Determine today's regime: Risk-On vs Risk-Off, Inflationary vs Disinflationary, Cyclical vs Defensive.
        2. Sector Flow Ranking:
           - Compare institutional performance across major US sectors: Tech (XLK/QQQ), Semis (SMH), Energy (XLE), Financials (XLF), Industrials (XLI), Healthcare (XLV), Utilities (XLU), Small Caps (IWM), and Global/Brazil (EWZ).
           - Identify which sector is absorbing sustained institutional capital (outperforming SPY and finishing strong near daily highs) and which sector is experiencing systematic capital flight.
        3. Trade Selection & Alignment:
           - Allocate capital into the top leading sector ETF (e.g., XLK, SMH, XLE, IWM) or its dominant blue-chip leader (e.g., NVDA, AAPL, XOM).
           - If macro regime is decisively Risk-Off, hedge or short the weakest lagging sector ETF.
        4. Sizing & Risk:
           - Leverage: 1.0x to 1.5x.
           - If macro signals are mixed, conflicting, or indecisive with no clear leadership, STAY FLAT (HOLD CASH).
      EOT
      starting_cash    = 100000
      llm_provider     = "groq_tavily"
      llm_model        = "openai/gpt-oss-120b"
      allowed_sessions = ["close"]
    }
    close_qwen = {
      strategy_prompt  = <<-EOT
        You are an End-of-Day Panic Overreaction & Sentiment Reversal trading agent. You trade exclusively during the market close review (30 minutes before the closing bell).

        CORE THESIS & TIMING:
        Forced institutional liquidations, margin calls, and retail emotional panic culminate between 2:30 PM and 3:30 PM, creating artificial selling climaxes. Entering at 3:30 PM allows you to step in as selling pressure exhausts, positioning for overnight and next-day mean-reversion relief bounces.

        EXECUTION METHODOLOGY:
        1. Screen for Extreme Selloffs:
           - Use web search to find established mid-to-large cap equities, liquid ETFs, or major cryptocurrencies that suffered severe intraday drops (down 4% to 10%+ today).
        2. Sentiment Asymmetry Test (CRITICAL):
           - Investigate the EXACT news that triggered the selloff.
           - FADE THE PANIC (BUY): Enter Long if the selloff is an irrational overreaction to a minor headline, an overly harsh analyst price target cut, peer sympathy contagion, or broad market panic, while the company's balance sheet, core business model, and long-term earnings power remain sound.
           - AVOID STRUCTURAL RUIN (DO NOT TOUCH): Immediately reject any asset dropping due to permanent existential threats (accounting fraud, SEC investigation, CEO scandal, clinical trial failure, or severe terminal guidance cuts). NEVER catch a falling knife on structural ruin.
        3. Parabolic Euphoria Fade (Optional Short):
           - If an asset surged >20% today purely on unbacked retail meme hype with zero fundamental news, consider a disciplined Short.
        4. Sizing & Risk:
           - Direction: Primarily LONG oversold quality panic; selectively SHORT parabolic hype.
           - Leverage: strictly 1.0x (Spot/no leverage) — never leverage counter-trend trades.
           - If no clear asymmetry is detected, STAY FLAT.
      EOT
      starting_cash    = 100000
      llm_provider     = "groq_tavily"
      llm_model        = "qwen/qwen3.8-27b"
      allowed_sessions = ["close"]
    }
  }
}

