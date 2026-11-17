from datetime import datetime, timezone
from uuid import UUID

from rbac_monitor.azure_client import ApiError, AzureClient
from rbac_monitor.events import RoleAssignmentEvent


def observe(event: RoleAssignmentEvent, client: AzureClient) -> dict[str, object]:
    assignment = client.get_assignment(event.role_assignment_id)
    properties = assignment["properties"]
    principal = properties.get("principalId")
    try:
        if not isinstance(principal, str) or str(UUID(principal)) != principal.lower():
            raise ValueError
    except ValueError:
        raise ApiError("roleAssignment", "invalidPrincipalId") from None
    kind = properties.get("principalType")
    scope = properties.get("scope")
    definition_id = properties.get("roleDefinitionId")
    expected_scope = event.role_assignment_id.rsplit("/", 4)[0]
    if (not isinstance(kind, str) or not kind.strip()
            or not isinstance(scope, str) or scope.lower() != expected_scope.lower()
            or not isinstance(definition_id, str)):
        raise ApiError("roleAssignment", "invalidResponse")
    definition = client.get_role_definition(definition_id)
    user = None
    status = "notApplicable"
    if kind.lower() == "user":
        user = client.get_user(principal)
        status = "companyNameNotSet" if user["companyName"] in (None, "") else "succeeded"
    return {
        "schemaVersion": 1, "eventName": "RbacRoleAssignmentObserved",
        "eventId": event.event_id, "correlationId": event.correlation_id,
        "eventTime": event.event_time,
        "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "subscriptionId": event.subscription_id, "roleAssignmentId": event.role_assignment_id,
        "principalId": principal, "principalType": kind,
        "roleDefinitionId": definition_id, "roleName": definition["properties"]["roleName"],
        "scope": scope, "displayName": user["displayName"] if user else None,
        "userPrincipalName": user["userPrincipalName"] if user else None,
        "companyName": user["companyName"] if user else None, "enrichmentStatus": status,
    }
