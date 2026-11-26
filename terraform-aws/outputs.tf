output "s3_signal_bucket" {
  description = "S3 bucket storing pre-scraped Fed and flow signal data"
  value       = aws_s3_bucket.signals.id
}

output "bot_lambda_names" {
  description = "Lambda function names for each bot"
  value       = { for k, v in aws_lambda_function.bot : k => v.function_name }
}

output "fed_scraper_lambda" {
  description = "Fed scraper Lambda function name"
  value       = aws_lambda_function.fed_scraper.function_name
}

output "flow_scraper_lambda" {
  description = "Flow scraper Lambda function name"
  value       = aws_lambda_function.flow_scraper.function_name
}
