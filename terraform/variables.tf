variable "aws_region" {
  type        = string
  default     = "ap-southeast-1"
  description = "AWS region to deploy the ECR repository"
}

variable "repository_name" {
  type        = string
  default     = "tf-2-ai-engine"
  description = "Name of the ECR repository"
}

variable "cdo_06_aws_account_id" {
  type        = string
  default     = "" # Placeholder - Thay thế bằng Account ID thật của CDO-06
  description = "AWS Account ID for CDO Platform 06"
}

variable "cdo_03_aws_account_id" {
  type        = string
  default     = "" # Placeholder - Thay thế bằng Account ID thật của CDO-03
  description = "AWS Account ID for CDO Platform 03"
}

variable "cdo_01_aws_account_id" {
  type        = string
  default     = "" # Placeholder - Thay thế bằng Account ID thật của CDO-01
  description = "AWS Account ID for CDO Platform 01"
}

variable "allowed_lambda_source_arns" {
  type        = list(string)
  default     = []
  description = "List of Lambda function ARNs in other accounts allowed to pull images from this ECR"
}


