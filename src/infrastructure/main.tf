resource "aws_iam_role" "mock_service_role" {
    name = "dos_mock_service_role"
    assume_role_policy = jsonencode({
        Version = "2012-10-17",
        Statement = [{
            Action = "sts:AssumeRole",
            Effect = "Allow",
            Principal = {
                Service = ["glue.amazonaws.com", "lambda.amazonaws.com", "sagemaker.amazonaws.com"]
            }
        }]
    })
}


#bronze layer
resource "aws_s3_bucket" "bronze_layer" {
    bucket = "dos-boeing-737-max-bronze-layer"
    tags = {
        Environment = "Local-dev"
        Project = "DOS-Boeing-737-max"
        Layer = "Bronze"  
        }
}

resource "aws_glue_job" "bronze_to_silver" {
    name = "bronze_to_silver_etl"
    role_arn = aws_iam_role.mock_service_role.arn
    command {
        name = "glueetl"
        script_location = "s3://dos-boeing-bronze-layer/scripts/bronze_to_silver.py"
    }
}

#silver layer 
resource "aws_s3_bucket" "silver_layer" {
    bucket = "dos-boeing-737-max-silver-layer"
    tags = {
        Environment = "Local-dev"
        Project = "DOS-Boeing-737-max"
        Layer = "Silver"  
        }
}

resource "aws_glue_job" "silver_to_gold" {
    name = "silver_to_gold_etl"
    role_arn = aws_iam_role.mock_service_role.arn
    command {
        name = "glueetl"
        script_location = "s3://dos-boeing-silver-layer/scripts/silver_to_gold.py"
    }
}

#gold layer
resource "aws_s3_bucket" "gold_layer" {
    bucket = "dos-boeing-737-max-gold-layer"
    tags = {
        Environment = "Local-dev"
        Project = "DOS-Boeing-737-max"
        Layer = "Gold"  
        }
}

resource "aws_s3_bucket" "athena_results" {
    bucket = "dos-boeing-737-max-athena-results"
}

resource "aws_athena_database" "athena_db" {
    name = "dos_boeing_737_max_db"
    bucket = aws_s3_bucket.athena_results.id
}

resource "aws_sagemaker_notebook_instance" "notebook_instance" {
    name = "dos-boeing-737-max-notebook"
    instance_type = "ml.t2.medium"
    role_arn = aws_iam_role.mock_service_role.arn
}


# Hot path (real-time anomaly pipeline) — see anomaly_pipeline.tf
# The previous placeholder Lambda (boeing_hot_path_alert) has been replaced
# by a Kinesis-triggered detector that publishes to SNS. The mock_service_role
# above already permits lambda.amazonaws.com to AssumeRole; the inline
# permissions policy for the new Lambda lives in anomaly_pipeline.tf.

