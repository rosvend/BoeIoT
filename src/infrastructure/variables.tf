variable "project_name" {
  description = "The base name for the project resources"
  type        = string
  default     = "dos-boeing-737-max"
}

variable "environment" {
  description = "Deploy environment (ej. local-dev, prod)"
  type        = string
  default     = "local-dev"
}

variable "aws_region" {
  description = "AWS Region"
  type        = string
  default     = "us-east-1"
}