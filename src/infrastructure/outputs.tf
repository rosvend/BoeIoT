output "bronze_bucket_name" {
  description = "Name of the Bronze layer S3 bucket for raw data ingestion"
  value       = module.medallion_lakehouse.bronze_bucket_name
}

output "silver_bucket_name" {
  description = "Name of the Silver layer S3 bucket for cleaned data"
  value       = module.medallion_lakehouse.silver_bucket_name
}

output "athena_database_name" {
  description = "Name of the Athena database"
  value       = module.medallion_lakehouse.athena_database_name
}

output "telemetry_stream_name" {
  description = "Kinesis stream where producers put telemetry frames"
  value       = module.anomaly_detector.telemetry_stream_name
}

output "anomaly_topic_arn" {
  description = "SNS topic the Lambda publishes anomaly alerts to"
  value       = module.anomaly_detector.anomaly_topic_arn
}

output "alerts_queue_url" {
  description = "SQS queue subscribed to the SNS topic — poll this in the demo consumer"
  value       = module.anomaly_detector.alerts_queue_url
}

output "anomaly_lambda_name" {
  description = "Name of the deployed anomaly detector Lambda"
  value       = module.anomaly_detector.anomaly_lambda_name
}
