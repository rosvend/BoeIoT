variable "project_name" {
  description = "Base name for all hot-path resources"
  type        = string
}

variable "environment" {
  description = "Deploy environment (e.g. local-dev, prod)"
  type        = string
}

variable "kinesis_shard_count" {
  description = "Shards on the telemetry stream"
  type        = number
}

variable "kinesis_retention_hours" {
  description = "Kinesis record retention in hours (24 = AWS/LocalStack minimum)"
  type        = number
}

variable "lambda_batch_size" {
  description = "Max Kinesis records per Lambda invocation"
  type        = number
}

variable "lambda_batch_window_seconds" {
  description = "Max seconds to buffer Kinesis records before invoking Lambda"
  type        = number
}

variable "lambda_endpoint_url_override" {
  description = "Optional AWS_ENDPOINT_URL injected into the Lambda environment; empty for both real AWS and LocalStack Community"
  type        = string
  default     = ""
}

variable "silver_bucket_id" {
  description = "ID of the Silver layer bucket holding the anomaly thresholds artifact"
  type        = string
}

variable "silver_bucket_arn" {
  description = "ARN of the Silver layer bucket, scoped in the Lambda S3 read policy"
  type        = string
}

variable "mock_service_role_id" {
  description = "ID of the shared mock service role the hot-path policy attaches to"
  type        = string
}

variable "mock_service_role_arn" {
  description = "ARN of the shared mock service role the Lambda assumes"
  type        = string
}

variable "lambda_source_dir" {
  description = "Path to the anomaly detector Lambda source directory"
  type        = string
}

variable "lambda_artifact_path" {
  description = "Output path for the packaged Lambda deployment zip"
  type        = string
}
