# Standalone and public-facing -- this is the feature shown on its own
# dashboard section, entirely separate from the bots' trading loop. Runs
# once a day, well before market open, so the day's report is ready by
# the time the opening-bell decision happens. Scans broadly (general +
# crypto + forex news, not just the three bot tickers) rather than being
# limited to any fixed watchlist. Runs on Gemini.
#
# Named "analysis" rather than "asymmetry" deliberately -- asymmetry
# detection is this feature's current analytical focus (see the prompt
# in functions/analysis-report), not a permanent property of the
# container/route/type names, which stay general enough to cover a
# different or broader analytical lens later without a rename.
resource "azurerm_linux_function_app" "analysis_report" {
  name                       = "func-analysis-report-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  service_plan_id            = azurerm_service_plan.consumption.id
  storage_account_name       = azurerm_storage_account.functions.name
  storage_account_access_key = azurerm_storage_account.functions.primary_access_key

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME" = "python"
    "FINNHUB_API_KEY"          = var.finnhub_api_key
    "GEMINI_API_KEY"           = var.gemini_api_key
    "GROQ_API_KEY"             = var.groq_api_key
    "TAVILY_API_KEY"           = var.tavily_api_key
    "COSMOS_ENDPOINT"          = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_KEY"               = azurerm_cosmosdb_account.main.primary_key
  }
}
