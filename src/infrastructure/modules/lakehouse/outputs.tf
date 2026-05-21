output "bronze_bucket_name" {
  description = "Name of the Bronze layer S3 bucket for raw data ingestion"
  value       = aws_s3_bucket.bronze_layer.bucket
}

output "bronze_bucket_arn" {
  description = "ARN of the Bronze layer S3 bucket"
  value       = aws_s3_bucket.bronze_layer.arn
}

output "silver_bucket_name" {
  description = "Name of the Silver layer S3 bucket for cleaned data"
  value       = aws_s3_bucket.silver_layer.bucket
}

output "silver_bucket_id" {
  description = "ID of the Silver layer S3 bucket"
  value       = aws_s3_bucket.silver_layer.id
}

output "silver_bucket_arn" {
  description = "ARN of the Silver layer S3 bucket"
  value       = aws_s3_bucket.silver_layer.arn
}

output "gold_bucket_name" {
  description = "Name of the Gold layer S3 bucket for curated data"
  value       = aws_s3_bucket.gold_layer.bucket
}

output "gold_bucket_arn" {
  description = "ARN of the Gold layer S3 bucket"
  value       = aws_s3_bucket.gold_layer.arn
}

output "athena_results_bucket_name" {
  description = "Name of the S3 bucket holding Athena query results"
  value       = aws_s3_bucket.athena_results.bucket
}

output "athena_database_name" {
  description = "Name of the Athena database"
  value       = aws_athena_database.athena_db.name
}

output "mock_service_role_id" {
  description = "ID of the shared mock service role consumed by downstream modules"
  value       = aws_iam_role.mock_service_role.id
}

output "mock_service_role_arn" {
  description = "ARN of the shared mock service role consumed by downstream modules"
  value       = aws_iam_role.mock_service_role.arn
}
