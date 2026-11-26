output "api_endpoint" {
  description = "HTTP API Gateway base URL"
  value       = aws_apigatewayv2_api.http_api.api_endpoint
}

output "orders_table_name" {
  value = aws_dynamodb_table.orders.name
}

output "receipts_bucket_name" {
  value = aws_s3_bucket.receipts.id
}

output "orders_queue_url" {
  value = aws_sqs_queue.orders.url
}

output "orders_dlq_url" {
  value = aws_sqs_queue.orders_dlq.url
}
