variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix for AWS resources."
  type        = string
  default     = "url-shortener"
}

variable "container_image" {
  description = "Fully qualified container image URI in ECR."
  type        = string
}

variable "base_url" {
  description = "Public base URL for generated short links."
  type        = string
}

variable "api_keys" {
  description = "Comma-separated API keys. Store securely in Terraform Cloud or pass via CI secret."
  type        = string
  sensitive   = true
}

variable "db_username" {
  description = "RDS PostgreSQL username."
  type        = string
  default     = "shortener"
}

variable "db_password" {
  description = "RDS PostgreSQL password."
  type        = string
  sensitive   = true
}

variable "db_backup_retention_period" {
  description = "RDS automated backup retention in days. Use 0 for free-tier constrained bootstrap accounts and 7+ for production accounts."
  type        = number
  default     = 0
}

variable "desired_api_count" {
  description = "Number of API tasks."
  type        = number
  default     = 2
}

variable "desired_worker_count" {
  description = "Number of worker tasks."
  type        = number
  default     = 1
}
