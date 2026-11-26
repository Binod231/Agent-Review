import json
import os
import time
import boto3

sqs = boto3.client("sqs")
ORDERS_QUEUE_URL = os.environ["ORDERS_QUEUE_URL"]


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    """v2 fast-ack producer: validates payload and enqueues to SQS (REL04-BP02)."""
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON payload"})

    order_id = payload.get("order_id")
    amount_cents = int(payload.get("amount_cents", 0))
    if not order_id or amount_cents <= 0:
        return _response(400, {"error": "order_id and positive amount_cents are required"})

    payload.setdefault("merchant_id", "merchant-default")
    payload.setdefault("run_id", "default")
    payload["enqueued_at"] = int(time.time() * 1000)

    sqs.send_message(
        QueueUrl=ORDERS_QUEUE_URL,
        MessageBody=json.dumps(payload),
    )

    return _response(202, {"status": "ACCEPTED", "order_id": order_id})
