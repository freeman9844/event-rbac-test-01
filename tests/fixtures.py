SUBSCRIPTION_ID = "11111111-1111-4111-8111-abcdef123456"
PRINCIPAL_ID = "22222222-2222-4222-8222-222222222222"
ASSIGNMENT_NAME = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ASSIGNMENT_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}"
    f"/providers/Microsoft.Authorization/roleAssignments/{ASSIGNMENT_NAME}"
)
ROLE_DEFINITION_ID = (
    f"/subscriptions/{SUBSCRIPTION_ID}"
    "/providers/Microsoft.Authorization/roleDefinitions/"
    "acdd72a7-3385-48ef-bd42-f606fba81ae7"
)


def event_envelope(role_assignment_id: str = ASSIGNMENT_ID) -> dict[str, object]:
    return {
        "id": "event-001",
        "eventTime": "2026-11-15T09:00:00Z",
        "eventType": "Microsoft.Resources.ResourceWriteSuccess",
        "topic": f"/subscriptions/{SUBSCRIPTION_ID}",
        "subject": role_assignment_id,
        "data": {
            "operationName": "Microsoft.Authorization/roleAssignments/write",
            "resourceUri": role_assignment_id,
            "correlationId": "correlation-001",
            "claims": {"name": "private-actor-claim"},
            "authorization": {"evidence": {"principalId": "not-the-recipient"}},
        },
    }
