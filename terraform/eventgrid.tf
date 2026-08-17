# Published to ONLY by the daily timer (market open + close). News
# collection, synthesis, and the daily analysis all write to Cosmos DB
# directly instead of through here -- Event Grid's only job in this
# project is "it's time for a bot to decide," nothing else.
resource "azurerm_eventgrid_topic" "trading" {
  name                = "evgt-trading-bots-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}
