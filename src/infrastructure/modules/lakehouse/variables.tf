variable "project_name" {
  description = "Base name for all lakehouse resources"
  type        = string
}

variable "environment" {
  description = "Deploy environment (e.g. local-dev, prod)"
  type        = string
}
