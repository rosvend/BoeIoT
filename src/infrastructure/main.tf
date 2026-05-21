module "medallion_lakehouse" {
  source = "./modules/lakehouse"

  project_name = var.project_name
  environment  = var.environment
}

module "anomaly_detector" {
  source = "./modules/anomalies"

  project_name                 = var.project_name
  environment                  = var.environment
  kinesis_shard_count          = var.kinesis_shard_count
  kinesis_retention_hours      = var.kinesis_retention_hours
  lambda_batch_size            = var.lambda_batch_size
  lambda_batch_window_seconds  = var.lambda_batch_window_seconds
  lambda_endpoint_url_override = var.lambda_endpoint_url_override

  silver_bucket_id      = module.medallion_lakehouse.silver_bucket_id
  silver_bucket_arn     = module.medallion_lakehouse.silver_bucket_arn
  mock_service_role_id  = module.medallion_lakehouse.mock_service_role_id
  mock_service_role_arn = module.medallion_lakehouse.mock_service_role_arn

  lambda_source_dir    = "${path.root}/../lambdas/anomaly_detector"
  lambda_artifact_path = "${path.root}/lambda_anomaly_detector.zip"
}
