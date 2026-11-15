import json
import logging
import os

import azure.functions as func

from rbac_monitor.events import parse_event

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

    logger.info(
        json.dumps(
            {
                "schemaVersion": 1,
                "eventName": "RbacRoleAssignmentReceived",
                "eventId": parsed.event_id,
                "roleAssignmentId": parsed.role_assignment_id,
            }
        )
    )
