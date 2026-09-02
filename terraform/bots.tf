# One independent Function App per entry in var.bots. Same codebase
# deployed N times; each instance reads its own prompt and model from
# its own app settings. Adding another bot later just means editing
# variables.tf and re-applying.
#
# Every bot gets all provider keys regardless of which one its
# llm_provider actually points to. Simpler Terraform, and the unused
# keys just sit there unused -- an acceptable tradeoff for a learning
# project; splitting keys per-bot is a reasonable hardening exercise
# later if this ever became something other than a sandbox.
resource "azurerm_linux_function_app" "bot" {
  for_each = var.bots

  name                       = "func-bot-${replace(each.key, "_", "-")}-${random_string.suffix.result}"
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
    "FUNCTIONS_WORKER_RUNTIME"    = "python"
    "BOT_NAME"                    = each.key
    "BOT_STRATEGY_PROMPT"         = each.value.strategy_prompt
    "BOT_STARTING_CASH"           = each.value.starting_cash
    "BOT_LLM_PROVIDER"            = each.value.llm_provider
    "BOT_LLM_MODEL"               = each.value.llm_model
    "FINNHUB_API_KEY"             = var.finnhub_api_key
    "GEMINI_API_KEY"              = var.gemini_api_key
    "GROQ_API_KEY"                = var.groq_api_key
    "TAVILY_API_KEY"              = var.tavily_api_key
    "COSMOS_ENDPOINT"             = azurerm_cosmosdb_account.main.endpoint
    "COSMOS_KEY"                  = azurerm_cosmosdb_account.main.primary_key
    "WEBPUBSUB_CONNECTION_STRING" = azurerm_web_pubsub.main.primary_connection_string
  }
}

# Every bot hears the same two events -- session/open and session/close --
# and makes one unrestricted portfolio-level decision for each review.
resource "azurerm_eventgrid_event_subscription" "bot" {
  for_each = var.bots

  name  = "evgs-${replace(each.key, "_", "-")}"
  scope = azurerm_eventgrid_topic.trading.id

  azure_function_endpoint {
    function_id = "${azurerm_linux_function_app.bot[each.key].id}/functions/on_event"
  }

  advanced_filter {
    string_in {
      key    = "subject"
      values = ["session/open", "session/close"]
    }
  }
}
