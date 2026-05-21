output "telemetry_stream_name" {
  description = "Kinesis stream where producers put telemetry frames"
  value       = aws_kinesis_stream.telemetry.name
}

output "telemetry_stream_arn" {
  description = "ARN of the telemetry Kinesis stream"
  value       = aws_kinesis_stream.telemetry.arn
}

output "anomaly_topic_arn" {
  description = "SNS topic the Lambda publishes anomaly alerts to"
  value       = aws_sns_topic.anomalies.arn
}

output "alerts_queue_url" {
  description = "SQS queue subscribed to the SNS topic — poll this in the demo consumer"
  value       = aws_sqs_queue.alerts.url
}

output "anomaly_lambda_name" {
  description = "Name of the deployed anomaly detector Lambda"
  value       = aws_lambda_function.anomaly_detector.function_name
}
