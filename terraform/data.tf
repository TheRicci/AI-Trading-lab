# --- Cosmos DB: the transactional source of truth for everything ---
# free_tier_enabled grants 1,000 RU/s + 25GB storage forever, one such
# account per subscription. The database uses shared (database-level)
# throughput so every container below draws from that same free pool
# instead of each needing its own dedicated throughput.
resource "azurerm_cosmosdb_account" "main" {
  name                = "cosmos-tradingbots-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  free_tier_enabled   = true

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }
}

resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "TradingBots"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  throughput          = 1000
}

# --- Bot-facing containers ---

# One portfolio document per bot: cash on hand + current holdings.
# partition_key_paths takes a list -- the provider added support for
# hierarchical (multi-level) partition keys, so the old single-string
# partition_key_path was removed in favour of this. A single-element
# list is a normal, non-hierarchical partition key, same behavior as
# before, just a different shape to declare it in.
resource "azurerm_cosmosdb_sql_container" "portfolios" {
  name                 = "Portfolios"
  resource_group_name  = azurerm_resource_group.main.name
  account_name         = azurerm_cosmosdb_account.main.name
  database_name        = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths  = ["/botName"]
}

# Every trade (or explicit hold), with the bot's reasoning attached.
resource "azurerm_cosmosdb_sql_container" "trades" {
  name                 = "Trades"
  resource_group_name  = azurerm_resource_group.main.name
  account_name         = azurerm_cosmosdb_account.main.name
  database_name        = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths  = ["/botName"]
}

# Snapshots of cash + holdings + a pre-computed totalValue, written every
# time a bot decides. totalValue is computed once at write time (the bot
# already has fresh prices in hand) rather than recalculated later from
# historical prices -- keeps the frontend's comparison chart simple and
# avoids needing historical candle data.
resource "azurerm_cosmosdb_sql_container" "portfolio_history" {
  name                 = "PortfolioHistory"
  resource_group_name  = azurerm_resource_group.main.name
  account_name         = azurerm_cosmosdb_account.main.name
  database_name        = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths  = ["/botName"]
}

# --- Collection + synthesis containers (bot-facing only, never public) ---

# Raw news items collected continuously by the news watcher.
resource "azurerm_cosmosdb_sql_container" "news_items" {
  name                 = "NewsItems"
  resource_group_name  = azurerm_resource_group.main.name
  account_name         = azurerm_cosmosdb_account.main.name
  database_name        = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths  = ["/ticker"]
}

# Per-ticker digests written by the synthesizer, every few hours. Plain
# "what happened" summaries only -- this is NOT where asymmetry-hunting
# lives; that's the separate, broader Analyses container below.
resource "azurerm_cosmosdb_sql_container" "digests" {
  name                 = "Digests"
  resource_group_name  = azurerm_resource_group.main.name
  account_name         = azurerm_cosmosdb_account.main.name
  database_name        = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths  = ["/ticker"]
}

# Real daily price observations, built up by the bots themselves at
# every decision -- Finnhub's free tier doesn't provide historical
# candles for US stocks (moved to paid tiers), so this project grows
# its own series over time instead of depending on that. Momentum and
# mean-reversion strategies compute real SMA/RSI from this; see
# functions/bot's SMA_PERIOD/RSI_PERIOD comments for the honest
# ramp-up timeline. Natural-key document ids (ticker-date-session) make
# every write an idempotent upsert, same trick as the news watcher.
resource "azurerm_cosmosdb_sql_container" "price_history" {
  name                 = "PriceHistory"
  resource_group_name  = azurerm_resource_group.main.name
  account_name         = azurerm_cosmosdb_account.main.name
  database_name        = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths  = ["/ticker"]
}

# --- Public-facing container ---

# One document per day: the daily analysis's overview + suggested ideas,
# with asymmetry-hunting as its main analytical focus -- but named more
# broadly since that focus is a choice made in the prompt, not a fixed
# property of the feature itself.
resource "azurerm_cosmosdb_sql_container" "analyses" {
  name                 = "Analyses"
  resource_group_name  = azurerm_resource_group.main.name
  account_name         = azurerm_cosmosdb_account.main.name
  database_name        = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths  = ["/reportDate"]
}

# Azure AI Search was evaluated here and deliberately removed -- see the
# README's "what's simplified, on purpose" section for the reasoning
# (short version: a small, days-old, single-source internal index added
# little over Gemini's own training knowledge, and dedup turned out to
# have a much simpler fix that doesn't need it at all).
