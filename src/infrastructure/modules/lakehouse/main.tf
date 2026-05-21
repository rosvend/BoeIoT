terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  common_tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_iam_role" "mock_service_role" {
  name = "${var.project_name}-mock-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = ["glue.amazonaws.com", "lambda.amazonaws.com", "sagemaker.amazonaws.com"]
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_s3_bucket" "bronze_layer" {
  bucket = "${var.project_name}-bronze-layer"
  tags   = merge(local.common_tags, { Layer = "Bronze" })
}

resource "aws_s3_bucket" "silver_layer" {
  bucket = "${var.project_name}-silver-layer"
  tags   = merge(local.common_tags, { Layer = "Silver" })
}

resource "aws_s3_bucket" "gold_layer" {
  bucket = "${var.project_name}-gold-layer"
  tags   = merge(local.common_tags, { Layer = "Gold" })
}

resource "aws_s3_bucket" "athena_results" {
  bucket = "${var.project_name}-athena-results"
  tags   = local.common_tags
}

resource "aws_glue_job" "bronze_to_silver" {
  name     = "bronze_to_silver_etl"
  role_arn = aws_iam_role.mock_service_role.arn

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.bronze_layer.id}/scripts/bronze_to_silver.py"
  }

  tags = local.common_tags
}

resource "aws_glue_job" "silver_to_gold" {
  name     = "silver_to_gold_etl"
  role_arn = aws_iam_role.mock_service_role.arn

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_bucket.silver_layer.id}/scripts/silver_to_gold.py"
  }

  tags = local.common_tags
}

resource "aws_athena_database" "athena_db" {
  name   = replace("${var.project_name}_db", "-", "_")
  bucket = aws_s3_bucket.athena_results.id
}

resource "aws_sagemaker_notebook_instance" "notebook_instance" {
  name          = "${var.project_name}-notebook"
  instance_type = "ml.t2.medium"
  role_arn      = aws_iam_role.mock_service_role.arn
  tags          = local.common_tags
}
