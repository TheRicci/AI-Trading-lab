# ── Scraper schedules ────────────────────────────────────────────────
# Fed scraper: every 2 hours during US market hours (Mon-Fri)
resource "aws_scheduler_schedule" "fed_scraper" {
  name       = "fed-scraper-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 11,13,15,17,19 ? * MON-FRI *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.fed_scraper.arn
    role_arn = aws_iam_role.scheduler_exec.arn

    input = jsonencode({ source = "scheduled" })
  }
}

resource "aws_lambda_permission" "fed_scraper_scheduler" {
  statement_id  = "AllowScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fed_scraper.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.fed_scraper.arn
}

# Flow scraper: offset by 1 hour from fed scraper
resource "aws_scheduler_schedule" "flow_scraper" {
  name       = "flow-scraper-schedule"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 12,14,16,18,20 ? * MON-FRI *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.flow_scraper.arn
    role_arn = aws_iam_role.scheduler_exec.arn

    input = jsonencode({ source = "scheduled" })
  }
}

resource "aws_lambda_permission" "flow_scraper_scheduler" {
  statement_id  = "AllowScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.flow_scraper.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.flow_scraper.arn
}

# ── Bot close-session schedules ──────────────────────────────────────
# One schedule per bot, all firing at 19:30 UTC (3:30 PM ET = 30 min
# before close). Same DST caveat as the Azure timer triggers.
resource "aws_scheduler_schedule" "bot_close" {
  for_each = var.bots

  name       = "bot-close-${replace(each.key, "_", "-")}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(30 19 ? * MON-FRI *)"
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_lambda_function.bot[each.key].arn
    role_arn = aws_iam_role.scheduler_exec.arn

    input = jsonencode({ session = "close" })
  }
}

resource "aws_lambda_permission" "bot_close_scheduler" {
  for_each = var.bots

  statement_id  = "AllowCloseScheduler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot[each.key].function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.bot_close[each.key].arn
}
