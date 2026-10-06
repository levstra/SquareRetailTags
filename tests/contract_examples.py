"""Example payloads copied from WestgateLabels/docs/central-api.openapi.yaml (v0.4.0)."""

UPDATE_COMMAND = {
    "updateId": "7f0d0d5c-0b7e-4d2a-9c1e-1f3b6a4d8e21",
    "storeId": "STORE-001",
    "tagId": "840001282A7E",
    "version": 42,
    "productId": "SQ-VARIATION-8H2K",
    "createdAt": "2026-09-05T14:03:00Z",
    "display": {
        "itemName": "2024 Topps Chrome Baseball",
        "variationName": "Hobby Box",
        "price": {"amount": "219.99", "currency": "USD"},
        "sku": "TC24-HBY",
    },
}

UNASSIGN_COMMAND = {
    "updateId": "0d7f4c3e-5f1a-4c8b-9b7e-2a6d8e1f3c44",
    "storeId": "STORE-001",
    "tagId": "840001282A7E",
    "version": 44,
    "createdAt": "2026-09-06T09:00:00Z",
    "action": "Unassign",
}

LOCATE_COMMAND = {
    "updateId": "3c1f6a2e-7d0b-4e9a-8b1c-5f2d9e7a1b44",
    "storeId": "STORE-001",
    "tagId": "840001282A7E",
    "action": "Locate",
    "seconds": 30,
    "createdAt": "2026-09-06T14:03:00Z",
}

SUCCEEDED_REPORT = {
    "updateId": "7f0d0d5c-0b7e-4d2a-9c1e-1f3b6a4d8e21",
    "tagId": "840001282A7E",
    "version": 42,
    "action": "Update",
    "status": "Succeeded",
    "statusAt": "2026-09-05T14:03:41Z",
    "attempt": 2,
    "apId": "0CNT",
    "failureCode": None,
    "failureMessage": None,
    "retryable": None,
    "diagnostics": {"rfPower": -58, "battery": 30, "temperature": 22, "statusByte": 129, "lowBattery": False},
}

TELEMETRY = {
    "storeId": "STORE-001",
    "reportedAt": "2026-09-06T14:10:00Z",
    "full": False,
    "labels": [{
        "tagId": "840001282A7E",
        "firstSeenAt": "2026-09-01T09:00:12Z",
        "lastSeenAt": "2026-09-06T14:09:47Z",
        "lastSeenApId": "0CNT",
        "rfPower": -48,
        "battery": 31,
        "lowBattery": False,
        "temperature": 23,
        "statusByte": 0,
        "firmwareVersion": 7,
        "type": 132, "size": 0, "color": 0, "factory": 0,
        "lastSuccessApId": "0CNT",
        "lastSuccessAt": "2026-09-06T13:58:20Z",
        "lastResultAt": "2026-09-06T13:58:20Z",
        "lastResultSuccess": True,
    }],
    "accessPoints": [{
        "apId": "0CNT", "connected": True, "stale": False, "connectedAt": "2026-09-06T08:00:01Z",
        "disconnectedAt": None, "lastHeartbeatAt": "2026-09-06T14:09:47Z", "heartbeatIntervalSeconds": 15,
        "apVersion": "1.0.28.0", "ip": "192.168.1.60", "waitCount": 0, "sendCount": 0,
    }],
}
