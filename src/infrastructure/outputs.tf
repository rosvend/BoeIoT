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