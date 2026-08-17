output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "event_grid_topic_endpoint" {
  value = azurerm_eventgrid_topic.trading.endpoint
}

output "daily_timer_function_name" {
  value = azurerm_linux_function_app.daily_timer.name
}

output "news_watcher_function_name" {
  value = azurerm_linux_function_app.news_watcher.name
}

output "synthesizer_function_name" {
  value = azurerm_linux_function_app.synthesizer.name
}

output "analysis_report_function_name" {
  value = azurerm_linux_function_app.analysis_report.name
}

output "bot_function_names" {
  value = { for k, v in azurerm_linux_function_app.bot : k => v.name }
}

output "api_function_name" {
  value = azurerm_linux_function_app.api.name
}

output "api_function_default_hostname" {
  value = azurerm_linux_function_app.api.default_hostname
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.main.endpoint
}

output "cosmos_primary_key" {
  value     = azurerm_cosmosdb_account.main.primary_key
  sensitive = true
}

output "web_pubsub_name" {
  value = azurerm_web_pubsub.main.name
}
