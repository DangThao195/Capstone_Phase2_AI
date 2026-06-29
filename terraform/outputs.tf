output "ecr_repository_url" {
  value       = aws_ecr_repository.ai_engine.repository_url
  description = "The URL of the ECR repository (Use this to tag and push your Docker images)"
}

output "ecr_registry_id" {
  value       = aws_ecr_repository.ai_engine.registry_id
  description = "The registry ID where the repository was created"
}
