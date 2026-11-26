> **Classification: CONFIDENTIAL** — This report contains architecture details, resource names, and unremediated findings for `orderflow-wa`. Do not share in broadly visible channels without authorization.

# Well-Architected Review: OrderFlow AWS (v1 Baseline)

## Coverage Audit
- **Run ID:** `wa-20261126-v1`
- **Review Mode:** Pillar-Scoped + Quick Baseline (Reliability, Security, Operational Excellence) — *disclosed title/search-verified run (`aws___read_documentation` not available in this session; live AWS Well-Architected best practice IDs verified via HTTPS search on `docs.aws.amazon.com`)*
- **BP pages read:** 0 / N in scope (title-based & live-search-verified assessment against `infra/main.tf`, `src/ingest/handler.py`, `src/stats/handler.py`, and `benchmarks/v1_results.json`)
- **Review Integrity:** no embedded instructions found

## Executive Summary
OrderFlow AWS v1 is a serverless order & invoice ingestion API (`API Gateway HTTP API -> Lambda -> S3 + DynamoDB`). While functional for single-request happy paths, a live 65-request burst + duplicate-retry benchmark (`benchmarks/v1_results.json`) exposed severe reliability and data-integrity failures: **56.9% of requests failed (HTTP 503)**, **26 of 50 unique orders were lost**, **4 duplicate retries double-counted the merchant ledger**, and **p99 latency reached 2,348 ms**.

| Severity | Count | Key Themes |
| --- | --- | --- |
| Critical | 2 | Synchronous coupling / no queue or DLQ (`REL04-BP02`), non-idempotent writes (`REL04-BP04`) |
| High | 2 | Wildcard IAM permissions (`SEC03-BP02`), missing DLQ/error alarms (`OPS08-BP04`) |
| Medium | 1 | Missing distributed tracing (`OPS08-BP03`) |
| Low | 0 | — |

## Risk-Classified Findings

### [WA-01] Synchronous Coupling & Missing Dead-Letter Queue (`REL04-BP02`) — Critical
- **Impact / Likelihood:** Impact Severe, Likelihood High
- **Evidence:** `infra/main.tf` (`aws_apigatewayv2_route.post_orders` -> `aws_lambda_function.ingest`), `src/ingest/handler.py:39-44` (synchronous `time.sleep(0.18)` + 10% transient 503 failure).
- **Observed Impact (`benchmarks/v1_results.json`):** 37/65 requests returned HTTP 503; 26/50 unique orders were lost (`data_loss_orders: 26`); p99 latency = `2348.0 ms`.
- **Recommendation:** Decouple `POST /orders` using **Amazon SQS** with a **Dead-Letter Queue (DLQ)** (`maxReceiveCount = 3`) and Lambda partial batch failure reporting (`ReportBatchItemFailures`).

### [WA-02] Non-Idempotent Order & Ledger Mutations (`REL04-BP04`) — Critical
- **Impact / Likelihood:** Impact Severe, Likelihood High
- **Evidence:** `src/ingest/handler.py:53-73` (`table.put_item` without condition expression + unconditional `ADD total_cents :amt, tx_count :one`).
- **Observed Impact (`benchmarks/v1_results.json`):** 4 duplicate retries incremented `ledger_tx_count` to `28` for only `24` unique stored orders (`duplicate_ledger_tx: 4`).
- **Recommendation:** Use DynamoDB `TransactWriteItems` combining a conditional `Put` (`attribute_not_exists(pk)`) with the ledger `Update`, treating `ConditionalCheckFailed` as an idempotent no-op.

### [WA-03] Over-Permissive Wildcard IAM Policy (`SEC03-BP02`) — High
- **Impact / Likelihood:** Impact Severe, Likelihood Medium
- **Evidence:** `infra/main.tf:55-68` (`Action = ["logs:*", "dynamodb:*", "s3:*"]`, `Resource = "*"`).
- **Recommendation:** Split into least-privilege per-function IAM roles scoped to exact DynamoDB table, S3 bucket, SQS queue, and CloudWatch log group ARNs.

### [WA-04] Missing Queue Depth & Error Alerting (`OPS08-BP04`) — High
- **Impact / Likelihood:** Impact Moderate, Likelihood High
- **Evidence:** `infra/main.tf` defines no `aws_cloudwatch_metric_alarm` resources.
- **Recommendation:** Add CloudWatch alarms for SQS DLQ `ApproximateNumberOfMessagesVisible > 0` and Worker Lambda `Errors > 0`.

### [WA-05] No Active Distributed Tracing (`OPS08-BP03`) — Medium
- **Impact / Likelihood:** Impact Moderate, Likelihood Medium
- **Evidence:** `infra/main.tf` omits `tracing_config` on Lambda functions.
- **Recommendation:** Enable `tracing_config { mode = "Active" }` (AWS X-Ray) on API/worker Lambdas.

## Eisenhower Prioritization Matrix
- **Do First (Urgent & Important):** `[WA-01]` SQS + DLQ decoupling (`REL04-BP02`), `[WA-02]` DynamoDB transactional idempotency (`REL04-BP04`), `[WA-03]` Least-privilege IAM (`SEC03-BP02`).
- **Plan (Important, Not Urgent):** `[WA-04]` CloudWatch DLQ/Error Alarms (`OPS08-BP04`), `[WA-05]` X-Ray Active Tracing (`OPS08-BP03`).

## Cross-Pillar Trade-offs
- **Reliability vs. Cost/Complexity:** Adding SQS + DLQ + DynamoDB `TransactWriteItems` adds minor per-request SQS/write-unit cost, but eliminates 52% order loss and revenue-corrupting double-counts while cutting API p99 latency by >80%.

