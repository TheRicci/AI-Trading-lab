# ── Bot Lambda functions ─────────────────────────────────────────────
# One Lambda per bot entry. Same codebase deployed N times; each reads
# its own configuration from environment variables.
# Run `./scripts/deploy_aws.sh` before `terraform apply` so the ZIPs in
# build-aws/ include Python dependencies (requests, azure-cosmos, boto3).
locals {
  bot_zip_path          = "${path.module}/../build-aws/bot-aws.zip"
  fed_scraper_zip_path  = "${path.module}/../build-aws/fed-scraper.zip"
  flow_scraper_zip_path = "${path.module}/../build-aws/flow-scraper.zip"
}

resource "aws_lambda_function" "bot" {
  for_each = var.bots

  function_name = "bot-${replace(each.key, "_", "-")}-${random_string.suffix.result}"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  memory_size   = 256
  timeout       = 120

  filename         = local.bot_zip_path
  source_code_hash = filebase64sha256(local.bot_zip_path)

  environment {
    variables = {
      BOT_NAME                = each.key
      BOT_STRATEGY_PROMPT     = each.value.strategy_prompt
      BOT_STARTING_CASH       = tostring(each.value.starting_cash)
      BOT_LLM_PROVIDER        = each.value.llm_provider
      BOT_LLM_MODEL           = each.value.llm_model
      GEMINI_API_KEY           = var.gemini_api_key
      GROQ_API_KEY             = var.groq_api_key
      FINNHUB_API_KEY          = var.finnhub_api_key
      TAVILY_API_KEY           = var.tavily_close_api_key
      TAVILY_OPEN_API_KEY      = var.tavily_open_api_key
      TAVILY_CLOSE_API_KEY     = var.tavily_close_api_key
      COSMOS_ENDPOINT          = var.cosmos_endpoint
      COSMOS_KEY               = var.cosmos_key
      S3_SIGNAL_BUCKET         = aws_s3_bucket.signals.id
    }
  }
}

# ── Scraper Lambda functions ─────────────────────────────────────────
resource "aws_lambda_function" "fed_scraper" {
  function_name = "fed-scraper-${random_string.suffix.result}"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  memory_size   = 256
  timeout       = 120

  filename         = local.fed_scraper_zip_path
  source_code_hash = filebase64sha256(local.fed_scraper_zip_path)

  environment {
    variables = {
      TAVILY_RESEARCH_API_KEY = var.tavily_research_api_key
      FINNHUB_API_KEY         = var.finnhub_api_key
      S3_SIGNAL_BUCKET        = aws_s3_bucket.signals.id
    }
  }
}

resource "aws_lambda_function" "flow_scraper" {
  function_name = "flow-scraper-${random_string.suffix.result}"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.11"
  memory_size   = 256
  timeout       = 120

  filename         = local.flow_scraper_zip_path
  source_code_hash = filebase64sha256(local.flow_scraper_zip_path)

  environment {
    variables = {
      TAVILY_RESEARCH_API_KEY = var.tavily_research_api_key
      FINNHUB_API_KEY         = var.finnhub_api_key
      S3_SIGNAL_BUCKET        = aws_s3_bucket.signals.id
    }
  }
}
