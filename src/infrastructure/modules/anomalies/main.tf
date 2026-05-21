terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

locals {
  common_tags = {
    Environment = var.environment
    Project     = var.project_name
    Layer       = "Hot-path"
  }
  telemetry_stream_name = "${var.project_name}-telemetry-stream"
  anomaly_topic_name    = "${var.project_name}-anomalies"
  alerts_queue_name     = "${var.project_name}-alerts"
  lambda_function_name  = "${var.project_name}-anomaly-detector"
  thresholds_key        = "artifacts/anomaly_thresholds.json"
}

resource "aws_kinesis_stream" "telemetry" {
  name             = local.telemetry_stream_name
  shard_count      = var.kinesis_shard_count
  retention_period = var.kinesis_retention_hours

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }

  tags = local.common_tags
}

resource "aws_sns_topic" "anomalies" {
  name = local.anomaly_topic_name
  tags = local.common_tags
}

resource "aws_sqs_queue" "alerts" {
  name                       = local.alerts_queue_name
  message_retention_seconds  = 345600
  visibility_timeout_seconds = 30
  tags                       = local.common_tags
}

resource "aws_sns_topic_subscription" "alerts_to_sqs" {
  topic_arn            = aws_sns_topic.anomalies.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.alerts.arn
  raw_message_delivery = true
}

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
    resources = ["${var.silver_bucket_arn}/artifacts/*"]
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
  role   = var.mock_service_role_id
  policy = data.aws_iam_policy_document.lambda_hot_path.json
}

data "archive_file" "anomaly_detector_zip" {
  type        = "zip"
  source_dir  = var.lambda_source_dir
  output_path = var.lambda_artifact_path
  # requirements.txt is intentionally empty so boto3 comes from the runtime.
  # If you add dependencies, run pip install --target before apply.
  excludes = ["__pycache__", "*.pyc", "requirements.txt"]
}

resource "aws_lambda_function" "anomaly_detector" {
  function_name = local.lambda_function_name
  role          = var.mock_service_role_arn
  runtime       = "python3.12"
  handler       = "lambda_handler.handler"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.anomaly_detector_zip.output_path
  source_code_hash = data.archive_file.anomaly_detector_zip.output_base64sha256

  environment {
    variables = {
      SNS_TOPIC_ARN     = aws_sns_topic.anomalies.arn
      THRESHOLDS_BUCKET = var.silver_bucket_id
      THRESHOLDS_KEY    = local.thresholds_key
      DETECTOR_TYPE     = "threshold"
      LOG_LEVEL         = "INFO"
      # Empty by default — LocalStack Community auto-routes SDK calls from
      # inside the Lambda sandbox back to itself, which is also correct for
      # real AWS. Override only for custom LocalStack setups.
      AWS_ENDPOINT_URL = var.lambda_endpoint_url_override
    }
  }

  tags = local.common_tags

  depends_on = [aws_iam_role_policy.lambda_hot_path]
}

resource "aws_lambda_event_source_mapping" "kinesis_to_lambda" {
  event_source_arn                   = aws_kinesis_stream.telemetry.arn
  function_name                      = aws_lambda_function.anomaly_detector.function_name
  starting_position                  = "LATEST"
  batch_size                         = var.lambda_batch_size
  maximum_batching_window_in_seconds = var.lambda_batch_window_seconds

  depends_on = [aws_iam_role_policy.lambda_hot_path]
}
