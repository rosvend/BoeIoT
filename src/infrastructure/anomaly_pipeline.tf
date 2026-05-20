# ─────────────────────────────────────────────────────────────────────────────
#  Hot-path real-time anomaly pipeline
#  Producer ──▶ Kinesis ──▶ Lambda ──▶ SNS ──▶ SQS (assertable subscriber)
#
#  Designed for LocalStack-first development (every endpoint is env-driven on
#  the producer/consumer side) and 1:1 portable to real AWS (no LocalStack-
#  specific resource types or hostnames in the resource definitions
#  themselves — only the optional Lambda env-var override pokes at endpoints).
# ─────────────────────────────────────────────────────────────────────────────

locals {
  hot_path_tags = {
    Environment = "Local-dev"
    Project     = "DOS-Boeing-737-max"
    Layer       = "Hot-path"
  }
  telemetry_stream_name = "${var.project_name}-telemetry-stream"
  anomaly_topic_name    = "${var.project_name}-anomalies"
  alerts_queue_name     = "${var.project_name}-alerts"
  lambda_function_name  = "boeing_anomaly_detector"
  thresholds_key        = "artifacts/anomaly_thresholds.json"
}

# ── Kinesis: ingest telemetry frames from producers ──────────────────────────
resource "aws_kinesis_stream" "telemetry" {
  name             = local.telemetry_stream_name
  shard_count      = var.kinesis_shard_count
  retention_period = var.kinesis_retention_hours

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = local.hot_path_tags
}

# ── SNS: fan-out anomaly alerts ──────────────────────────────────────────────
resource "aws_sns_topic" "anomalies" {
  name = local.anomaly_topic_name
  tags = local.hot_path_tags
}

# ── SQS: assertable demo subscriber ──────────────────────────────────────────
# An SQS queue subscribes to the SNS topic so the demo (and any test) can
# poll for alerts deterministically. In production this queue can sit next
# to email / Lambda / HTTP subscribers without architectural change.
resource "aws_sqs_queue" "alerts" {
  name                       = local.alerts_queue_name
  message_retention_seconds  = 345600 # 4 days
  visibility_timeout_seconds = 30
  tags                       = local.hot_path_tags
}

resource "aws_sns_topic_subscription" "alerts_to_sqs" {
  topic_arn            = aws_sns_topic.anomalies.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.alerts.arn
  raw_message_delivery = true
}

# Allow the SNS topic to deliver to the SQS queue.
data "aws_iam_policy_document" "alerts_queue_policy" {
  statement {
    sid     = "AllowSNSPublish"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]
    principals {
      type        = "Service"
      identifiers = ["sns.amazonaws.com"]
    }
    resources = [aws_sqs_queue.alerts.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_sns_topic.anomalies.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "alerts_allow_sns" {
  queue_url = aws_sqs_queue.alerts.id
  policy    = data.aws_iam_policy_document.alerts_queue_policy.json
}

# ── IAM: permissions for the Lambda (attached to the shared mock role) ───────
data "aws_iam_policy_document" "lambda_hot_path" {
  statement {
    sid    = "KinesisReadStream"
    effect = "Allow"
    actions = [
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:ListShards",
      "kinesis:ListStreams",
    ]
    resources = [aws_kinesis_stream.telemetry.arn]
  }
  statement {
    sid       = "SnsPublishAnomalies"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.anomalies.arn]
  }
  statement {
    sid       = "S3ReadThresholdsArtifact"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.silver_layer.arn}/artifacts/*"]
  }
  statement {
    sid    = "CloudWatchLogsWrite"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "lambda_hot_path" {
  name   = "${var.project_name}-lambda-hot-path"
  role   = aws_iam_role.mock_service_role.id
  policy = data.aws_iam_policy_document.lambda_hot_path.json
}

# ── Lambda package: zip the anomaly_detector source dir ──────────────────────
data "archive_file" "anomaly_detector_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/anomaly_detector"
  output_path = "${path.module}/lambda_anomaly_detector.zip"
  # Keep the package tiny: requirements.txt is intentionally empty so boto3
  # comes from the runtime. If you add deps, run pip install --target before
  # apply (or switch to a Terraform null_resource that builds the zip).
  excludes = ["__pycache__", "*.pyc", "requirements.txt"]
}

# ── Lambda: the anomaly detector itself ──────────────────────────────────────
resource "aws_lambda_function" "anomaly_detector" {
  function_name = local.lambda_function_name
  role          = aws_iam_role.mock_service_role.arn
  runtime       = "python3.12"
  handler       = "lambda_handler.handler"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.anomaly_detector_zip.output_path
  source_code_hash = data.archive_file.anomaly_detector_zip.output_base64sha256

  environment {
    variables = {
      SNS_TOPIC_ARN     = aws_sns_topic.anomalies.arn
      THRESHOLDS_BUCKET = aws_s3_bucket.silver_layer.id
      THRESHOLDS_KEY    = local.thresholds_key
      DETECTOR_TYPE     = "threshold"
      LOG_LEVEL         = "INFO"
      # Empty by default — LocalStack Community auto-routes SDK calls from
      # inside the Lambda sandbox back to itself, so leaving this unset is
      # correct for both LocalStack and real AWS. Override only for custom
      # LocalStack setups (e.g. host.docker.internal).
      AWS_ENDPOINT_URL = var.lambda_endpoint_url_override
    }
  }

  tags = local.hot_path_tags

  depends_on = [aws_iam_role_policy.lambda_hot_path]
}

# ── Event source mapping: Kinesis → Lambda ───────────────────────────────────
resource "aws_lambda_event_source_mapping" "kinesis_to_lambda" {
  event_source_arn                   = aws_kinesis_stream.telemetry.arn
  function_name                      = aws_lambda_function.anomaly_detector.function_name
  starting_position                  = "LATEST"
  batch_size                         = var.lambda_batch_size
  maximum_batching_window_in_seconds = var.lambda_batch_window_seconds

  depends_on = [aws_iam_role_policy.lambda_hot_path]
}
