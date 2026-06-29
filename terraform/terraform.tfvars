# =============================================================================
# Terraform Variables Configuration — TF2 AIOps AI Engine ECR
# =============================================================================
# Hướng dẫn: Copy file này thành "terraform.tfvars" và điền các giá trị thực tế.
# LƯU Ý: Không commit file "terraform.tfvars" chứa thông tin nhạy cảm lên Git.
# =============================================================================

# AWS Region nơi deploy ECR (Singapore)
aws_region = "ap-southeast-1"

# Tên ECR Repository đồng bộ với Deployment Contract
repository_name = "tf-2-ai-engine"

# AWS Account ID thật của CDO Platform 06 (gồm 12 chữ số)
cdo_06_aws_account_id = "093490087544"

# AWS Account ID thật của CDO Platform 03 (gồm 12 chữ số)
cdo_03_aws_account_id = "812527291603"

# AWS Account ID thật của CDO Platform 01 (gồm 12 chữ số)
cdo_01_aws_account_id = "994899741781"

# Các Lambda function ARN từ các CDO teams được phép pull image ECR
allowed_lambda_source_arns = [
  "arn:aws:lambda:ap-southeast-1:093490087544:function:tf2-finops-*-ai-request"
]


