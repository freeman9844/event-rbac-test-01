import json
import logging
import os

import azure.functions as func
import requests
from azure.identity import ManagedIdentityCredential

from rbac_monitor.azure_client import ApiError, AzureClient
from rbac_monitor.events import parse_event
from rbac_monitor.processor import observe

app = func.FunctionApp()
logger = logging.getLogger("rbac_monitor")


@app.function_name(name="RbacAssignmentObserver")
@app.event_grid_trigger(arg_name="event")
def handle_role_assignment(event: func.EventGridEvent) -> None:
    try:
        expected_subscription_id = os.environ.get("EXPECTED_SUBSCRIPTION_ID")
        if not expected_subscription_id:
            raise ValueError("EXPECTED_SUBSCRIPTION_ID must be configured")
        parsed = parse_event(
            {
                "id": event.id,
                "eventType": event.event_type,
                "eventTime": event.event_time.isoformat() if event.event_time else None,
                "topic": event.topic,
                "subject": event.subject,
                "data": event.get_json(),
            },
            expected_subscription_id,
        )
    except ValueError as error:
        logger.error(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "eventName": "RbacRoleAssignmentEnrichmentFailed",
                    "eventId": event.id,
                    "stage": "validation",
                    "code": str(error),
                }
            )
        )
        raise

    enrichment = os.environ.get("ENABLE_IDENTITY_ENRICHMENT", "false").lower()
    if enrichment not in ("true", "false"):
        logger.error(json.dumps({
            "schemaVersion": 1, "eventName": "RbacRoleAssignmentEnrichmentFailed",
            "eventId": parsed.event_id, "roleAssignmentId": parsed.role_assignment_id,
            "stage": "configuration", "code": "invalidIdentityEnrichmentConfiguration",
        }))
        raise ValueError("ENABLE_IDENTITY_ENRICHMENT must be true or false")

    logger.info(json.dumps({
        "schemaVersion": 1,
        "eventName": "RbacRoleAssignmentReceived",
        "eventId": parsed.event_id,
        "roleAssignmentId": parsed.role_assignment_id,
    }))
    if enrichment == "false":
        return
    try:
        with ManagedIdentityCredential() as credential, requests.Session() as session:
            result = observe(parsed, AzureClient(credential, session=session))
        logger.info(json.dumps(result))
    except ApiError as error:
        logger.error(json.dumps({
            "schemaVersion": 1, "eventName": "RbacRoleAssignmentEnrichmentFailed",
            "eventId": parsed.event_id, "roleAssignmentId": parsed.role_assignment_id,
            "stage": error.stage, "code": error.code, "httpStatus": error.status_code,
            "serviceRequestId": error.request_id,
        }))
        raise
