terraform {
  required_version = ">= 1.11.0, < 2.0.0"
}

# Week 1 intentionally creates no cloud resources and requires no credentials.
# Azure resources are a later, separately approved and budgeted milestone.
variable "platform_name" {
  description = "Non-secret project identifier reserved for future infrastructure."
  type=string
  default     = "ingen-fleet-ops"
}

output "platform_name" {
  description = "Project identifier for the future infrastructure module."
  value       = var.platform_name
}
