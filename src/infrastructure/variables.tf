variable "project_name" {
  description = "The base name for the project resources"
  type        = string
  default     = "dos-boeing-737-max"
}

variable "environment" {
  description = "Deploy environment (e.g. local-dev, prod)"
  type        = string
  default     = "local-dev"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "kinesis_shard_count" {
  description = "Shards on the telemetry stream. 1 is plenty for LocalStack demos."
  type        = number
  default     = 1
}

variable "kinesis_retention_hours" {
  description = "Kinesis record retention in hours (24 = LocalStack/AWS minimum)."
  type        = number
  default     = 24
}

variable "lambda_batch_size" {
  description = "Max Kinesis records per Lambda invocation."
  type        = number
  default     = 10
}

variable "lambda_batch_window_seconds" {
  description = "Max seconds to buffer Kinesis records before invoking Lambda."
  type        = number
  default     = 1
}

variable "lambda_endpoint_url_override" {
  description = <<-EOT
    Optional AWS_ENDPOINT_URL passed into the Lambda environment. Leave empty
    for both real AWS and the LocalStack Community runtime: LocalStack
    transparently proxies SDK calls back to itself from inside its Lambda
    sandbox, so no override is needed. Set only for a custom LocalStack setup
    (e.g. http://host.docker.internal:4566).
  EOT
  type        = string
  default     = ""
}
