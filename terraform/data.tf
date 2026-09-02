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

# Every trade (or explicit hold), with the bot's reasoning attached.
resource "azurerm_cosmosdb_sql_container" "trades" {
  name                = "Trades"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/botName"]
}

# The single source of truth for each bot's portfolio. Every review writes one
# FULL snapshot -- cash, holdings, positions, NAV, starting cash and the
# strategy/provider/model metadata -- plus a timestamp. The NEWEST row for a
# bot IS its current state; the earlier rows are its history. totalValue is
# computed once at write time (the bot already has fresh prices in hand) rather
# than recalculated later from historical prices, keeping the frontend's
# comparison chart simple and avoiding the need for historical candle data.
resource "azurerm_cosmosdb_sql_container" "portfolio_history" {
  name                = "PortfolioHistory"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/botName"]
}

# --- Public-facing container ---

# One document per day: the daily analysis's overview + suggested ideas,
# with asymmetry-hunting as its main analytical focus -- but named more
# broadly since that focus is a choice made in the prompt, not a fixed
# property of the feature itself.
resource "azurerm_cosmosdb_sql_container" "analyses" {
  name                = "Analyses"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/reportDate"]
}

# Azure AI Search was evaluated here and deliberately removed -- see the
# README's "what's simplified, on purpose" section for the reasoning
# (short version: a small, days-old, single-source internal index added
# little over Gemini's own training knowledge, and dedup turned out to
# have a much simpler fix that doesn't need it at all).
