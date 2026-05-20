output "bronze_bucket_name" {
  description = "The name of the Bronze layer S3 bucket for raw data ingestion"
  value       = aws_s3_bucket.bronze_layer.bucket
}

output "silver_bucket_name" {
  description = "The name of the Silver layer S3 bucket for cleaned data"
  value       = aws_s3_bucket.silver_layer.bucket
}

output "athena_database_name" {
  description = "The name of the Athena database"
  value       = aws_athena_database.athena_db.name
}

# ── Hot-path outputs (consumed by demo scripts and the README quickstart) ────

output "telemetry_stream_name" {
  description = "Kinesis stream where producers put telemetry frames."
  value       = aws_kinesis_stream.telemetry.name
}

output "anomaly_topic_arn" {
  description = "SNS topic the Lambda publishes anomaly alerts to."
  value       = aws_sns_topic.anomalies.arn
}

output "alerts_queue_url" {
  description = "SQS queue subscribed to the SNS topic — poll this in the demo consumer."
  value       = aws_sqs_queue.alerts.url
}

output "anomaly_lambda_name" {
  description = "Name of the deployed anomaly detector Lambda."
  value       = aws_lambda_function.anomaly_detector.function_name
}
