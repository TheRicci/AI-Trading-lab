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

variable "tavily_api_key" {
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
    strategy_prompt = string
    starting_cash   = number
    llm_provider    = string
    llm_model       = string
  }))

  default = {
    open_gemini = {
      strategy_prompt = "You are an autonomous trading agent. You can choose whatever style fits today's conditions best, including trend-following, mean reversion, catalyst/event-driven, valuation, volatility breakout, sentiment, or staying out entirely. Use web search whenever it helps you gather live market context, recent news, filings, earnings, macro events, technical levels, or other relevant metrics."
      starting_cash   = 100000
      llm_provider    = "gemini"
      llm_model       = "gemini-3.6-flash"
    }
    open_gptoss = {
      strategy_prompt = "You are an autonomous trading agent. You can choose whatever style fits today's conditions best, including trend-following, mean reversion, catalyst/event-driven, valuation, volatility breakout, sentiment, or staying out entirely. Use web search whenever it helps you gather live market context, recent news, filings, earnings, macro events, technical levels, or other relevant metrics."
      starting_cash   = 100000
      llm_provider    = "groq_tavily"
      llm_model       = "openai/gpt-oss-120b"
    }
    open_qwen = {
      strategy_prompt = "You are an autonomous trading agent. You can choose whatever style fits today's conditions best, including trend-following, mean reversion, catalyst/event-driven, valuation, volatility breakout, sentiment, or staying out entirely. Use web search whenever it helps you gather live market context, recent news, filings, earnings, macro events, technical levels, or other relevant metrics."
      starting_cash   = 100000
      llm_provider    = "groq_tavily"
      llm_model       = "qwen/qwen3.8-27b"
    }
  }
}

