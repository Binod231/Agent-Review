import json
import os
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb")
ORDERS_TABLE = os.environ["ORDERS_TABLE"]


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    run_id = params.get("run_id", "default")
    merchant_id = params.get("merchant_id", "merchant-acme")

    table = dynamodb.Table(ORDERS_TABLE)

    ledger_resp = table.get_item(
        Key={"pk": f"LEDGER#{run_id}", "sk": f"MERCHANT#{merchant_id}"}
    )
    ledger = ledger_resp.get("Item", {})

    # Scan/count orders for this run_id (fine for small benchmark runs)
    orders_resp = table.scan(
        FilterExpression=Key("run_id").eq(run_id) & Key("pk").begins_with("ORDER#")
    )
    orders = orders_resp.get("Items", [])

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "run_id": run_id,
                "merchant_id": merchant_id,
                "unique_orders_stored": len(orders),
                "ledger_tx_count": ledger.get("tx_count", 0),
                "ledger_total_cents": ledger.get("total_cents", 0),
            },
            cls=DecimalEncoder,
        ),
    }

