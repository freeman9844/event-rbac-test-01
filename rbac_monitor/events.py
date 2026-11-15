from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote
from uuid import UUID


class EventValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RoleAssignmentEvent:
    event_id: str
    event_time: str
    correlation_id: str | None
    subscription_id: str
    role_assignment_id: str


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventValidationError(code)
    return value


def _assignment_id(value: object, subscription_id: str) -> str:
    resource_id = _text(value, "invalidRoleAssignmentId")
    decoded = unquote(resource_id)
    parts = resource_id.split("/")
    if (
        len(parts) < 7
        or parts[0] != ""
        or parts[1].casefold() != "subscriptions"
        or parts[2].casefold() != subscription_id
        or [part.casefold() for part in parts[-4:-1]]
        != ["providers", "microsoft.authorization", "roleassignments"]
        or any(part in ("", ".", "..") for part in decoded.split("/")[1:])
        or len(parts) != len(decoded.split("/"))
        or any(character in decoded for character in ("\\", "?", "#", "%"))
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
    ):
        raise EventValidationError("invalidRoleAssignmentId")
    try:
        assignment_guid = str(UUID(parts[-1]))
    except ValueError:
        raise EventValidationError("invalidRoleAssignmentId") from None
    if assignment_guid != parts[-1].casefold():
        raise EventValidationError("invalidRoleAssignmentId")
    return resource_id


def parse_event(
    envelope: Mapping[str, object], expected_subscription_id: str
) -> RoleAssignmentEvent:
    try:
        subscription_id = str(UUID(expected_subscription_id))
    except (ValueError, AttributeError, TypeError):
        raise EventValidationError("invalidExpectedSubscriptionId") from None
    if not isinstance(envelope, Mapping):
        raise EventValidationError("invalidEnvelope")
    if _text(envelope.get("eventType"), "invalidEventType").casefold() != (
        "microsoft.resources.resourcewritesuccess"
    ):
        raise EventValidationError("unexpectedEventType")
    topic = _text(envelope.get("topic"), "invalidSourceTopic")
    if topic.casefold() != f"/subscriptions/{subscription_id}":
        raise EventValidationError("unexpectedSourceTopic")
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        raise EventValidationError("invalidEventData")
    if _text(data.get("operationName"), "invalidOperationName").casefold() != (
        "microsoft.authorization/roleassignments/write"
    ):
        raise EventValidationError("unexpectedOperationName")

    subject = envelope.get("subject")
    resource_uri = data.get("resourceUri")
    if subject is None or subject == "":
        role_assignment_id = _assignment_id(resource_uri, subscription_id)
    else:
        role_assignment_id = _assignment_id(subject, subscription_id)
        if resource_uri is not None:
            validated_uri = _assignment_id(resource_uri, subscription_id)
            if validated_uri.casefold() != role_assignment_id.casefold():
                raise EventValidationError("conflictingResourceIds")

    event_id = _text(envelope.get("id"), "invalidEventId")
    time_text = _text(envelope.get("eventTime"), "invalidEventTime")
    try:
        event_time = datetime.fromisoformat(time_text.replace("Z", "+00:00"))
    except ValueError:
        raise EventValidationError("invalidEventTime") from None
    if event_time.tzinfo is None:
        raise EventValidationError("invalidEventTime")
    correlation_id = data.get("correlationId")
    if correlation_id is not None and not isinstance(correlation_id, str):
        raise EventValidationError("invalidCorrelationId")

    return RoleAssignmentEvent(
        event_id=event_id,
        event_time=event_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        correlation_id=correlation_id,
        subscription_id=subscription_id,
        role_assignment_id=role_assignment_id,
    )
