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
  description = "API key for Finnhub (free tier, no card required) -- used for quotes and general/company news"
  type        = string
  sensitive   = true
}

# --- Two providers now, by design: same three strategies run on each,
# so performance differences can be attributed to strategy vs. brain
# separately. Gemini is also used exclusively by the synthesizer and
# daily analysis functions (see synthesizer.tf / analysis.tf), since
# those run far more often than the twice-daily bot decisions and
# Gemini's free tier has the most daily headroom.
variable "gemini_api_key" {
  description = "API key for Gemini, from a billing-disabled Google Cloud project"
  type        = string
  sensitive   = true
}

variable "groq_api_key" {
  description = "API key from console.groq.com -- powers the Qwen-branded bots (qwen/qwen3.6-27b, Groq's current recommended Qwen model)"
  type        = string
  sensitive   = true
}

variable "tickers" {
  description = "Universe of tickers the news watcher and synthesizer track -- the union of every bot's tickers below"
  type        = string
  default     = "AAPL,MSFT,NVDA"
}

variable "bots" {
  description = <<-EOT
    One entry per independent bot. Each becomes its own Function App and
    Event Grid subscription. strategy_key selects which real technical
    indicator (if any) gets computed for that bot -- momentum (SMA),
    mean_reversion (RSI), or event_driven (none, weighs news instead).
    llm_provider is gemini or groq; llm_model is the provider-specific
    model string. Three strategies x two providers = six bots, so every
    strategy runs on both brains for a clean comparison.
  EOT
  type = map(object({
    strategy_prompt = string
    strategy_key    = string
    tickers         = list(string)
    starting_cash   = number
    llm_provider    = string
    llm_model       = string
  }))

  default = {
    # ---------- Momentum / trend-following ----------
    # Real, well-documented strategy family: momentum effects are one of
    # the most-studied anomalies in academic finance (Jegadeesh & Titman,
    # 1993, is the classic reference), and trend-following has decades
    # of practitioner use among CTAs. The bot is handed a real computed
    # short-term SMA (see functions/bot) and told to act on trend
    # persistence, not asked to estimate one itself.
    momentum_gemini = {
      strategy_prompt = "You are a momentum (trend-following) trader. This is a real, well-documented strategy family: extensive academic research and decades of practitioner use show that assets exhibiting a recent price trend tend to continue that trend more often than reversing, over the near term. Act on this: when price is above its short-term moving average and showing positive momentum, favor buying or holding; when it's below the average and losing ground, favor selling or staying out. You are not a fundamentals investor -- mostly ignore news unless it's dramatic enough to break the technical trend."
      strategy_key    = "momentum"
      tickers         = ["AAPL", "MSFT", "NVDA"]
      starting_cash   = 100000
      llm_provider    = "gemini"
      llm_model       = "gemini-2.0-flash" # check ai.google.dev for the current recommended free-tier Flash model name
    }
    momentum_qwen = {
      strategy_prompt = "You are a momentum (trend-following) trader. This is a real, well-documented strategy family: extensive academic research and decades of practitioner use show that assets exhibiting a recent price trend tend to continue that trend more often than reversing, over the near term. Act on this: when price is above its short-term moving average and showing positive momentum, favor buying or holding; when it's below the average and losing ground, favor selling or staying out. You are not a fundamentals investor -- mostly ignore news unless it's dramatic enough to break the technical trend."
      strategy_key    = "momentum"
      tickers         = ["AAPL", "MSFT", "NVDA"]
      starting_cash   = 100000
      llm_provider    = "groq"
      llm_model       = "qwen/qwen3.6-27b" # Groq's current recommended Qwen model -- check console.groq.com/docs/models if this changes
    }

    # ---------- Mean-reversion / RSI ----------
    # Real, well-documented strategy family: the Relative Strength Index
    # (Welles Wilder, 1978) is one of the most widely taught and widely
    # used technical indicators for identifying overbought/oversold
    # conditions. The bot is handed a real computed RSI value, not
    # asked to estimate one.
    mean_reversion_gemini = {
      strategy_prompt = "You are a mean-reversion trader using the Relative Strength Index (RSI), a well-established technical indicator used to identify when a price move has become stretched relative to its recent trading range. Classic thresholds: RSI below 30 suggests an asset is oversold (a potential buying opportunity as price reverts upward); RSI above 70 suggests overbought (a potential selling opportunity as price reverts downward). Act on these thresholds -- you are betting against short-term extremes, not following them."
      strategy_key    = "mean_reversion"
      tickers         = ["AAPL", "MSFT", "NVDA"]
      starting_cash   = 100000
      llm_provider    = "gemini"
      llm_model       = "gemini-2.0-flash"
    }
    mean_reversion_qwen = {
      strategy_prompt = "You are a mean-reversion trader using the Relative Strength Index (RSI), a well-established technical indicator used to identify when a price move has become stretched relative to its recent trading range. Classic thresholds: RSI below 30 suggests an asset is oversold (a potential buying opportunity as price reverts upward); RSI above 70 suggests overbought (a potential selling opportunity as price reverts downward). Act on these thresholds -- you are betting against short-term extremes, not following them."
      strategy_key    = "mean_reversion"
      tickers         = ["AAPL", "MSFT", "NVDA"]
      starting_cash   = 100000
      llm_provider    = "groq"
      llm_model       = "qwen/qwen3.6-27b"
    }

    # ---------- Event-driven / catalyst ----------
    # Real, well-documented strategy family: event-driven investing is
    # an established hedge-fund strategy classification built around
    # trading specific catalysts (earnings, M&A, guidance, litigation)
    # rather than technical price patterns. Deliberately selective by
    # design -- absent a real catalyst, holding is the expected default,
    # not a failure to act.
    event_driven_gemini = {
      strategy_prompt = "You are an event-driven (catalyst) trader, a real hedge-fund strategy classification built around trading specific news events -- earnings surprises, M&A activity, guidance changes, litigation, leadership changes -- rather than technical price patterns. Weigh the news digest and any flagged global analysis heavily. Absent a genuine catalyst, your default is to hold and wait: this strategy is inherently selective, not always-active, and holding on a quiet day is the strategy working correctly, not a failure to act."
      strategy_key    = "event_driven"
      tickers         = ["AAPL", "MSFT", "NVDA"]
      starting_cash   = 100000
      llm_provider    = "gemini"
      llm_model       = "gemini-2.0-flash"
    }
    event_driven_qwen = {
      strategy_prompt = "You are an event-driven (catalyst) trader, a real hedge-fund strategy classification built around trading specific news events -- earnings surprises, M&A activity, guidance changes, litigation, leadership changes -- rather than technical price patterns. Weigh the news digest and any flagged global analysis heavily. Absent a genuine catalyst, your default is to hold and wait: this strategy is inherently selective, not always-active, and holding on a quiet day is the strategy working correctly, not a failure to act."
      strategy_key    = "event_driven"
      tickers         = ["AAPL", "MSFT", "NVDA"]
      starting_cash   = 100000
      llm_provider    = "groq"
      llm_model       = "qwen/qwen3.6-27b"
    }
  }
}
