# Agent-Review (OrderFlow AWS)

A serverless order & invoice ingestion pipeline built from scratch on AWS (`API Gateway HTTP API`, `AWS Lambda`, `Amazon SQS + DLQ`, `Amazon DynamoDB`, `Amazon S3`, `Amazon CloudWatch`, and `AWS X-Ray`) to demonstrate a measurable before-and-after architecture transformation driven by the **Agent Toolkit Well-Architected Review** skill (`aws/agent-toolkit-for-aws`).

## Architecture: Before vs. After

### v1 Baseline (Pre-Review)
- **Flow:** `API Gateway (POST /orders)` → synchronous `ingest` Lambda → `S3` + `DynamoDB`
- **Flaws surfaced by `aws-well-architected-review`:**
  - **`REL04-BP02` (Critical):** Synchronous coupling with no queue buffer or Dead-Letter Queue (DLQ) — burst traffic and transient downstream failures drop orders.
  - **`REL04-BP04` (Critical):** Non-idempotent `put_item` / `update_item` — duplicate webhook retries double-count merchant revenue.
  - **`SEC03-BP02` (High):** Over-permissive wildcard (`*`) IAM policy.
  - **`OPS08-BP04` / `OPS08-BP03` (High/Medium):** Missing DLQ/error CloudWatch alarms and active X-Ray tracing.

### v2 Hardened (Post-Review)
- **Flow:** `API Gateway (POST /orders)` → **Direct SQS Integration (`SQS-SendMessage`)** → `SQS Queue` (+ `DLQ`, `maxReceiveCount = 3`) → `worker` Lambda (`ReportBatchItemFailures`) → `S3` + **Atomic DynamoDB `TransactWriteItems`** (`attribute_not_exists(pk)`).
- **Security & Observability:** Least-privilege per-function IAM roles, CloudWatch alarms on DLQ depth and worker errors, and AWS X-Ray active tracing.

## Live AWS Benchmark (50 Unique Orders + 15 Duplicate Retries @ 25 Concurrency)

| Metric | v1 (Before Review) | v2 (After Review) |
| --- | --- | --- |
| **API 2xx Success Rate** | `43.1%` (28/65, 37× HTTP 503) | **`100.0%` (65/65)** |
| **Unique Orders Stored** | `24 / 50` (26 lost) | **`50 / 50` (0 lost)** |
| **Duplicate Ledger Tx** | `4` double-counted | **`0` duplicates** |
| **Merchant Ledger Total** | `$700.00` (`-$550.00` drift) | **`$1,250.00` (`$0.00` drift)** |

## Repository Layout
- `infra/` — Terraform IaC (`main.tf`, `variables.tf`, `outputs.tf`)
- `src/ingest/` — v1 synchronous handler (kept for reference; bypassed in v2 via API Gateway Direct SQS)
- `src/worker/` — v2 SQS batch worker with `ReportBatchItemFailures` & DynamoDB transactional idempotency
- `src/stats/` — `GET /stats` Lambda for real-time order & ledger verification
- `scripts/benchmark.py` — Live burst + duplicate-retry benchmark harness
- `aws-well-architected-review-report.md` — Well-Architected Review report

## Quick Start

```bash
cd infra
terraform init
terraform apply -auto-approve

# Run the live burst + duplicate-retry benchmark
cd ..
python3 scripts/benchmark.py \
  --api-url "$(terraform -chdir=infra output -raw api_endpoint)" \
  --label demo --orders 50 --duplicates 15 --concurrency 25 --wait-seconds 45
```
