# Terraform AWS Production Stack

This Terraform stack provisions the scaled production architecture:

- VPC with public and private subnets.
- NAT gateway for private ECS task egress.
- Application Load Balancer.
- ECS Fargate API service.
- ECS Fargate worker service.
- RDS PostgreSQL.
- ElastiCache Redis with encryption enabled.
- CloudWatch log group.
- Secrets Manager secrets for `API_KEYS` and `DATABASE_URL`.
- ECS service autoscaling for API tasks.

## Prerequisites

- Terraform 1.6+.
- AWS credentials with permissions for VPC, ECS, RDS, ElastiCache, IAM, ALB, CloudWatch, and Secrets Manager.
- A container image already pushed to ECR.

## Deploy

Create a local variable file:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit the values, then run:

```bash
terraform init
terraform plan
terraform apply
```

## Production Notes

- The stack uses two API tasks by default.
- API tasks and worker tasks run in private subnets.
- RDS is private and has deletion protection enabled.
- Redis is private and has encryption enabled.
- API traffic enters through the ALB.
- ALB health checks use `/healthz`.
- Application readiness is exposed at `/readyz`.
- GitHub Actions initializes the S3 backend dynamically. For local Terraform runs, pass matching `-backend-config` values for the state bucket, key, region, encryption, and lock file.
- `db_backup_retention_period` defaults to `0` so free-tier constrained AWS accounts can create the first database. This disables automated backups; use `7` or higher for a full production account.
