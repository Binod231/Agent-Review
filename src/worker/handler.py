import json
import os
import random
import time
import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")

ORDERS_TABLE = os.environ["ORDERS_TABLE"]
RECEIPTS_BUCKET = os.environ["RECEIPTS_BUCKET"]


def _process_order(payload: dict) -> None:
    order_id = payload["order_id"]
    merchant_id = payload.get("merchant_id", "merchant-default")
    amount_cents = int(payload["amount_cents"])
    run_id = payload.get("run_id", "default")

    # Simulate the same downstream enrichment latency & 10% transient failure rate
    time.sleep(0.18)
    if payload.get("force_transient_fail") or random.random() < 0.10:
        raise RuntimeError(f"Transient downstream failure for {order_id}")

    s3_key = f"receipts/{run_id}/{order_id}.json"
    s3.put_object(
        Bucket=RECEIPTS_BUCKET,
        Key=s3_key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )

    # v2 fix (REL04-BP04): Atomic idempotent transaction — retries never double-count!
    try:
        dynamodb.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": ORDERS_TABLE,
                        "Item": {
                            "pk": {"S": f"ORDER#{order_id}"},
                            "sk": {"S": f"RUN#{run_id}"},
                            "order_id": {"S": order_id},
                            "run_id": {"S": run_id},
                            "merchant_id": {"S": merchant_id},
                            "amount_cents": {"N": str(amount_cents)},
                            "s3_key": {"S": s3_key},
                            "status": {"S": "PROCESSED"},
                            "processed_at": {"N": str(int(time.time() * 1000))},
                        },
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Update": {
                        "TableName": ORDERS_TABLE,
                        "Key": {
                            "pk": {"S": f"LEDGER#{run_id}"},
                            "sk": {"S": f"MERCHANT#{merchant_id}"},
                        },
                        "UpdateExpression": "ADD total_cents :amt, tx_count :one",
                        "ExpressionAttributeValues": {
                            ":amt": {"N": str(amount_cents)},
                            ":one": {"N": "1"},
                        },
                    }
                },
            ]
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            reasons = exc.response.get("CancellationReasons", [])
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                # Idempotent duplicate — already processed, acknowledge cleanly
                print(json.dumps({"event": "duplicate_skipped", "order_id": order_id}))
                return
        raise


def lambda_handler(event, context):
    """v2 SQS worker with partial batch failure reporting (REL04-BP02)."""
    batch_item_failures = []
    for record in event.get("Records", []):
        msg_id = record["messageId"]
        try:
            payload = json.loads(record["body"])
            _process_order(payload)
        except Exception as exc:
            print(json.dumps({"event": "record_failed", "messageId": msg_id, "error": str(exc)}))
            batch_item_failures.append({"itemIdentifier": msg_id})

    return {"batchItemFailures": batch_item_failures}

