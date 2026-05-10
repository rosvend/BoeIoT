provider "aws" { 
    region = "us-east-1"
    access_key = "test"
    secret_key = "test"
    skip_credentials_validation = true
    skip_requesting_account_id = true
    skip_metadata_api_check = true
}

resource "aws_s3_bucket_tags" "bronze_layer" {
    bucket = aws_s3_bucket.bronze_layer.id
    tags = {
        Environment = "Local-dev"
        Project = "DOS-Boeing-737-max"
        Layer = "Bronze"  
        }
}