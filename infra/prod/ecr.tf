resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "MUTABLE" # :prod and :candidate move; :<sha> tags never do
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Never expire the images :prod and :candidate point at"
        selection    = { tagStatus = "tagged", tagPatternList = ["prod", "candidate"], countType = "imageCountMoreThan", countNumber = 9999 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the 30 most recent images"
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
        action       = { type = "expire" }
      },
    ]
  })
}
