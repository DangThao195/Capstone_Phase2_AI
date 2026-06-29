data "aws_caller_identity" "current" {}

locals {
  # Danh sách tất cả account IDs khai báo
  raw_accounts = [
    var.cdo_06_aws_account_id,
    var.cdo_03_aws_account_id,
    var.cdo_01_aws_account_id
  ]

  # Lọc bỏ các account ID trống hoặc là placeholder giả định
  active_accounts = [
    for acct in local.raw_accounts : acct
    if acct != "" && acct != "111122223333" && acct != "444455556666"
  ]

  # Nếu không có account nào của CDO hợp lệ, dùng chính account đang chạy deploy làm fallback
  # để tránh lỗi AWS "Principal not found" khi validate chính sách.
  principals = length(local.active_accounts) > 0 ? local.active_accounts : [data.aws_caller_identity.current.account_id]
}

resource "aws_ecr_repository" "ai_engine" {
  name                 = var.repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
  }

  tags = {
    TaskForce   = "2"
    Environment = "production"
    Owner       = "AI-Team"
    Project     = "FinOps-AIOps"
  }
}

resource "aws_ecr_lifecycle_policy" "ai_engine_lifecycle" {
  repository = aws_ecr_repository.ai_engine.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the last 10 tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v"]
          countType     = "imageCountMoreThan"
          countNumber   = 10
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images older than 7 days"
        selection = {
          tagStatus     = "untagged"
          countType     = "sinceImagePushed"
          countUnit     = "days"
          countNumber   = 7
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "aws_ecr_repository_policy" "ai_engine_policy" {
  repository = aws_ecr_repository.ai_engine.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid    = "AllowCrossAccountPull"
          Effect = "Allow"
          Principal = {
            AWS = [
              for acct in local.principals : "arn:aws:iam::${acct}:root"
            ]
          }
          Action = [
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer"
          ]
        }
      ],
      length(var.allowed_lambda_source_arns) > 0 ? [
        {
          Sid    = "AllowLambdaCrossAccountPull"
          Effect = "Allow"
          Principal = {
            Service = "lambda.amazonaws.com"
          }
          Action = [
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer"
          ]
          Condition = {
            StringLike = {
              "aws:SourceArn" = var.allowed_lambda_source_arns
            }
          }
        }
      ] : []
    )
  })
}
