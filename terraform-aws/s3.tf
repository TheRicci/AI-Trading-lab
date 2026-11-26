# Signal cache bucket — stores pre-scraped Fed sentiment and options flow
# data as daily JSON files. The bots read this before each review to get
# enriched context without burning Tavily credits inside the LLM loop.
resource "aws_s3_bucket" "signals" {
  bucket = "trading-bots-signals-${random_string.suffix.result}"
}

resource "aws_s3_bucket_public_access_block" "signals" {
  bucket                  = aws_s3_bucket.signals.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "signals" {
  bucket = aws_s3_bucket.signals.id

  rule {
    id     = "expire-old-signals"
    status = "Enabled"

    filter {}

    expiration {
      days = 30
    }
  }
}
