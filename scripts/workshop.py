"""Cloud Shell workshop support; never changes the default Azure subscription."""

import argparse
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4


class WorkshopError(RuntimeError):
    pass


def run_az(args: list[str], *, allow_empty: bool = False) -> object:
    executable = shutil.which("az")
    if not executable:
        raise WorkshopError("Azure CLI is missing. Install it or use Cloud Shell Bash.")
    try:
        result = subprocess.run(
            [executable, *args, "--output", "json", "--only-show-errors"],
            capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkshopError(f"Azure CLI failed: {error}") from error
    if result.returncode:
        raise WorkshopError(f"Azure CLI exit {result.returncode}: {result.stderr.strip()}")
    if allow_empty and not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise WorkshopError("Azure CLI did not return valid JSON.") from error


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkshopError(message)


def uuid_value(value: object) -> str:
    require(isinstance(value, str), "Expected a UUID string.")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise WorkshopError("Invalid UUID.") from error
    require(str(parsed) == value.lower(), "Expected a canonical UUID.")
    return str(parsed)


def utc_time(value: object) -> datetime:
    require(isinstance(value, str), "Expected an explicit UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkshopError("Invalid UTC timestamp.") from error
    require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), "Timestamp must be UTC.")
    return parsed


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_state(state: object) -> dict:
    keys = {
        "schemaVersion", "runId", "subscriptionId", "tenantId", "userObjectId",
        "resourceGroupName", "namePrefix", "location", "createdAtUtc",
        "deploymentNames", "outputs", "assignment",
    }
    require(isinstance(state, dict) and set(state) == keys, "Unexpected state fields.")
    require(type(state["schemaVersion"]) is int and state["schemaVersion"] == 1,
            "Unsupported state version; do not reconstruct an old run.")
    run_id = state["runId"]
    require(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{8}", run_id) is not None,
            "Invalid run ID.")
    for key in ("subscriptionId", "tenantId", "userObjectId"):
        uuid_value(state[key])
    require(state["resourceGroupName"] == f"rg-rbac-workshop-{run_id}", "Foreign group.")
    require(state["namePrefix"] == "rbac-lab", "Unexpected name prefix.")
    require(isinstance(state["location"], str)
            and re.fullmatch(r"[a-z][a-z0-9]{1,39}", state["location"]) is not None,
            "Invalid location.")
    require(state["deploymentNames"] == {
        "infra": f"rbac-ws-{run_id}-infra", "events": f"rbac-ws-{run_id}-events",
    }, "Foreign deployment names.")
    utc_time(state["createdAtUtc"])
    require(isinstance(state["outputs"], dict), "Invalid deployment outputs.")
    if state["outputs"]:
        validate_outputs(state, state["outputs"])
    assignment = state["assignment"]
    if assignment is not None:
        require(isinstance(assignment, dict) and set(assignment) == {
            "name", "id", "requestedAtUtc", "created", "removed",
        }, "Invalid assignment record.")
        name = uuid_value(assignment["name"])
        require(isinstance(assignment["id"], str) and assignment["id"].lower() == (
            group_scope(state) + "/providers/Microsoft.Authorization/roleAssignments/" + name
        ).lower(), "Foreign test assignment.")
        require(type(assignment["created"]) is bool and type(assignment["removed"]) is bool,
                "Invalid assignment lifecycle flags.")
        require(utc_time(assignment["requestedAtUtc"]) >= utc_time(state["createdAtUtc"]),
                "Assignment predates this run.")
    return state


def validate_outputs(state: dict, outputs: object) -> dict:
    keys = {"functionAppName", "functionAppResourceId", "managedIdentityPrincipalId",
            "applicationInsightsName", "workspaceResourceId", "systemTopicName",
            "subscriptionReaderAssignmentId"}
    require(isinstance(outputs, dict) and set(outputs) == keys
            and all(isinstance(value, str) for value in outputs.values()),
            "Unexpected deployment outputs.")
    match = re.fullmatch(r"func-rbac-lab-([a-z0-9]{13})", outputs["functionAppName"])
    require(match is not None, "Unexpected function name.")
    suffix = match.group(1)
    scope = group_scope(state)
    expected = {
        "functionAppResourceId": scope + "/providers/Microsoft.Web/sites/"
        + outputs["functionAppName"],
        "workspaceResourceId": scope + "/providers/Microsoft.OperationalInsights/workspaces/"
        + f"log-rbac-lab-{suffix}",
        "applicationInsightsName": f"appi-rbac-lab-{suffix}",
        "systemTopicName": f"evgt-rbac-lab-{suffix}",
    }
    for key, value in expected.items():
        require(outputs[key].lower() == value.lower(), f"Foreign output: {key}")
    uuid_value(outputs["managedIdentityPrincipalId"])
    reader = outputs["subscriptionReaderAssignmentId"]
    if reader:
        prefix = (f"/subscriptions/{state['subscriptionId']}/providers/"
                  "Microsoft.Authorization/roleAssignments/")
        require(reader.lower().startswith(prefix.lower()), "Foreign subscription Reader.")
        uuid_value(reader[len(prefix):])
    return outputs


def load_state(path: Path) -> dict:
    try:
        return validate_state(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkshopError(f"Cannot read state {path}: {error}") from error


def save_state(path: Path, state: dict) -> None:
    validate_state(state)
    save_json(path, state)


def save_json(path: Path, value: object) -> None:
    require(not path.is_symlink(), "Refusing a symbolic-link state file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def scoped(state: dict, args: list[str], *, allow_empty: bool = False) -> object:
    command = [*args, "--subscription", state["subscriptionId"]]
    return run_az(command, allow_empty=True) if allow_empty else run_az(command)


def group_scope(state: dict) -> str:
    return (f"/subscriptions/{state['subscriptionId']}/resourceGroups/"
            f"{state['resourceGroupName']}")


def ownership_tags(state: dict) -> dict:
    return {"workshop": "rbac-event", "runId": state["runId"], "managedBy": "workshop"}


def check_identity(state: dict) -> str:
    account = scoped(state, ["account", "show"])
    require(isinstance(account, dict)
            and account.get("id", "").lower() == state["subscriptionId"].lower()
            and account.get("tenantId", "").lower() == state["tenantId"].lower()
            and account.get("user", {}).get("type") == "user", "Wrong account or tenant.")
    user = scoped(state, ["rest", "--method", "get", "--url",
                          "https://graph.microsoft.com/v1.0/me?$select=id"])
    require(isinstance(user, dict), "Invalid signed-in user response.")
    return uuid_value(user.get("id"))


def owned_group(state: dict) -> bool:
    exists = scoped(state, ["group", "exists", "--name", state["resourceGroupName"]])
    require(type(exists) is bool, "Cannot determine whether the group exists.")
    if not exists:
        return False
    group = scoped(state, ["group", "show", "--name", state["resourceGroupName"]])
    require(isinstance(group, dict)
            and group.get("id", "").lower() == group_scope(state).lower(),
            "Unexpected resource group response.")
    tags = group.get("tags")
    require(isinstance(tags, dict)
            and all(tags.get(k) == v for k, v in ownership_tags(state).items()),
            "Resource group ownership mismatch. Nothing will be deleted.")
    return True


def check_run(state: dict) -> None:
    validate_state(state)
    require(check_identity(state) == state["userObjectId"].lower(), "Wrong signed-in user.")
    owned_group(state)


def init_run(root: Path, subscription_id: str, tenant_id: str, location: str) -> Path:
    run_id = secrets.token_hex(4)
    state = {
        "schemaVersion": 1, "runId": run_id,
        "subscriptionId": uuid_value(subscription_id), "tenantId": uuid_value(tenant_id),
        "userObjectId": "00000000-0000-0000-0000-000000000000",
        "resourceGroupName": f"rg-rbac-workshop-{run_id}", "namePrefix": "rbac-lab",
        "location": location, "createdAtUtc": now_utc(),
        "deploymentNames": {
            "infra": f"rbac-ws-{run_id}-infra", "events": f"rbac-ws-{run_id}-events",
        },
        "outputs": {}, "assignment": None,
    }
    validate_state(state)
    state["userObjectId"] = check_identity(state)
    source = f"/subscriptions/{state['subscriptionId']}"
    endpoint = source + "/providers/Microsoft.EventGrid/systemTopics"
    url = f"https://management.azure.com{endpoint}?api-version=2025-02-15"
    visited = set()
    while url:
        parsed = urlparse(url)
        require(parsed.scheme == "https" and parsed.netloc == "management.azure.com"
                and parsed.path.lower() == endpoint.lower() and url not in visited,
                "Unsafe or repeated system-topic pagination URL.")
        visited.add(url)
        page = scoped(state, ["rest", "--method", "get", "--url", url])
        require(isinstance(page, dict) and isinstance(page.get("value"), list),
                "Cannot list system topics.")
        for topic in page["value"]:
            require(isinstance(topic, dict) and isinstance(topic.get("properties"), dict),
                    "Malformed system topic.")
            topic_source = topic["properties"].get("source")
            require(isinstance(topic_source, str), "Missing system-topic source.")
            require(topic_source.lower() != source.lower(),
                    "A system topic already monitors this subscription. Use a separate "
                    "lab subscription; do not change or delete the existing topic.")
        url = page.get("nextLink")
        require(url is None or isinstance(url, str), "Invalid pagination URL.")
    require(not owned_group(state), "New run group already exists.")
    path = root / ".workshop-state" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            json.dump(state, stream, indent=2)
            stream.write("\n")
    except FileExistsError as error:
        raise WorkshopError("Run state already exists; refusing to overwrite it.") from error
    return path


def arm_get(state: dict, resource_id: str, version: str) -> dict:
    value = scoped(state, ["rest", "--method", "get", "--url",
                          f"https://management.azure.com{resource_id}?api-version={version}"])
    require(isinstance(value, dict), "Invalid ARM resource response.")
    return value


def capture_deployment(state_file: Path, deployment_name: str) -> None:
    state = load_state(state_file)
    require(deployment_name in state["deploymentNames"].values(), "Foreign deployment name.")
    check_run(state)
    require(owned_group(state), "Deployment group is missing.")
    deployment = scoped(state, ["deployment", "sub", "show", "--name", deployment_name])
    require(isinstance(deployment, dict)
            and deployment.get("properties", {}).get("provisioningState") == "Succeeded",
            "Deployment has not succeeded; inspect it before continuing.")
    raw_outputs = deployment["properties"].get("outputs")
    require(isinstance(raw_outputs, dict)
            and all(isinstance(item, dict) and "value" in item
                    for item in raw_outputs.values()), "Missing deployment outputs.")
    outputs = validate_outputs(state, {key: item["value"] for key, item in raw_outputs.items()})
    app = arm_get(state, outputs["functionAppResourceId"], "2024-11-01")
    require(app.get("id", "").lower() == outputs["functionAppResourceId"].lower()
            and app.get("identity", {}).get("principalId", "").lower()
            == outputs["managedIdentityPrincipalId"].lower(),
            "Deployment managed identity does not belong to this function.")
    previous_reader = state["outputs"].get("subscriptionReaderAssignmentId", "")
    if previous_reader and previous_reader.lower() != outputs["subscriptionReaderAssignmentId"].lower():
        require(find_role(role_assignments(state), previous_reader) is None,
                "The recorded subscription Reader still exists. Keep this state and "
                "clean the run before replacing its deployment outputs.")
    state["outputs"] = outputs
    save_state(state_file, state)


def package_sources(root: Path, destination: Path) -> list[str]:
    root = root.resolve()
    module = root / "rbac_monitor"
    require(module.is_dir() and not module.is_symlink(), "Missing or symbolic source package.")
    paths = [root / name for name in ("function_app.py", "host.json", "requirements.txt")]
    for path in module.rglob("*"):
        require(not path.is_symlink(), "Refusing symbolic package contents.")
        if path.suffix == ".py" and path.is_file():
            paths.append(path)
    require((module / "__init__.py") in paths and (module / "events.py") in paths,
            "Receiver Python sources are missing.")
    for path in paths:
        require(path.is_file() and not path.is_symlink()
                and path.resolve().is_relative_to(root), "Unsafe or missing package source.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            name = path.relative_to(root).as_posix()
            archive.write(path, name)
            names.append(name)
    return names


READER_ROLE = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
ROLE_API = "2022-04-01"
METRICS = ("PublishSuccessCount", "MatchedEventCount",
           "DeliverySuccessCount", "DeliveryAttemptFailCount")


def subscription_scope(state: dict) -> str:
    return f"/subscriptions/{state['subscriptionId']}"


def role_definition(state: dict) -> str:
    return subscription_scope(state) + "/providers/Microsoft.Authorization/roleDefinitions/" + READER_ROLE


def role_assignments(state: dict) -> list[dict]:
    endpoint = subscription_scope(state) + "/providers/Microsoft.Authorization/roleAssignments"
    url = f"https://management.azure.com{endpoint}?api-version={ROLE_API}"
    visited = set()
    result = []
    while url:
        parsed = urlparse(url)
        require(parsed.scheme == "https" and parsed.netloc == "management.azure.com"
                and parsed.path.lower() == endpoint.lower() and url not in visited,
                "Unsafe or repeated role-assignment pagination URL.")
        visited.add(url)
        page = scoped(state, ["rest", "--method", "get", "--url", url])
        require(isinstance(page, dict) and isinstance(page.get("value"), list),
                "Cannot list role assignments.")
        for item in page["value"]:
            require(isinstance(item, dict) and isinstance(item.get("id"), str)
                    and isinstance(item.get("properties"), dict), "Malformed role assignment.")
            result.append(item)
        url = page.get("nextLink")
        require(url is None or isinstance(url, str), "Invalid role pagination.")
    return result


def find_role(roles: list[dict], resource_id: str) -> dict | None:
    matches = [item for item in roles if item["id"].lower() == resource_id.lower()]
    require(len(matches) <= 1, "Ambiguous role assignment response.")
    return matches[0] if matches else None


def validate_role(state: dict, role: dict, resource_id: str,
                  principal: str, scope: str, principal_type: str) -> None:
    properties = role.get("properties", {})
    expected = {"principalId": principal, "scope": scope,
                "roleDefinitionId": role_definition(state), "principalType": principal_type}
    require(role.get("id", "").lower() == resource_id.lower()
            and all(isinstance(properties.get(key), str)
                    and properties[key].lower() == value.lower()
                    for key, value in expected.items()),
            "Role ownership mismatch. Refusing to delete an unverified assignment.")


def wait_absent(is_present: Callable[[], bool], description: str) -> None:
    deadline = time.monotonic() + 180
    while is_present():
        require(time.monotonic() < deadline, f"Timeout waiting for {description} deletion.")
        time.sleep(3)


def delete_role(state: dict, resource_id: str) -> None:
    scoped(state, ["rest", "--method", "delete", "--url",
                  f"https://management.azure.com{resource_id}?api-version={ROLE_API}"],
           allow_empty=True)
    wait_absent(lambda: find_role(role_assignments(state), resource_id) is not None,
                resource_id)


def remove_test_role(state_file: Path, state: dict) -> None:
    owned_group(state)
    assignment = state["assignment"]
    role = find_role(role_assignments(state), assignment["id"])
    if role is not None:
        validate_role(state, role, assignment["id"], state["userObjectId"],
                      group_scope(state), "User")
        delete_role(state, assignment["id"])
    assignment["removed"] = True
    save_state(state_file, state)


def exercise_event(state_file: Path) -> dict:
    state = load_state(state_file)
    check_run(state)
    require(owned_group(state) and bool(state["outputs"]), "Capture the infrastructure first.")
    require(state["assignment"] is None, "A test is already recorded; verify or clean that run.")
    for role in role_assignments(state):
        properties = role["properties"]
        require(not (
            str(properties.get("scope", "")).lower() == group_scope(state).lower()
            and str(properties.get("principalId", "")).lower() == state["userObjectId"].lower()
            and str(properties.get("roleDefinitionId", "")).lower() == role_definition(state).lower()
        ), "An existing direct Reader is present. It will not be replaced or deleted.")
    name = str(uuid4())
    assignment = {"name": name, "id": group_scope(state)
                  + "/providers/Microsoft.Authorization/roleAssignments/" + name,
                  "requestedAtUtc": now_utc(), "created": False, "removed": False}
    state["assignment"] = assignment
    save_state(state_file, state)
    failure = None
    try:
        response = scoped(state, ["rest", "--method", "put", "--url",
                                 f"https://management.azure.com{assignment['id']}?api-version={ROLE_API}",
                                 "--body", json.dumps({"properties": {
                                     "principalId": state["userObjectId"], "principalType": "User",
                                     "roleDefinitionId": role_definition(state),
                                 }})])
        require(isinstance(response, dict), "Missing role creation response.")
        validate_role(state, response, assignment["id"], state["userObjectId"],
                      group_scope(state), "User")
        assignment["created"] = True
        save_state(state_file, state)
    except (WorkshopError, OSError) as error:
        failure = error
    finally:
        try:
            remove_test_role(state_file, state)
        except (WorkshopError, OSError) as error:
            if failure is not None:
                raise WorkshopError(f"{failure}; cleanup also failed: {error}") from error
            raise
    if failure is not None:
        raise failure
    return assignment


def write_evidence(state_file: Path, state: dict, name: str, value: object) -> None:
    directory = state_file.parent / state["runId"]
    require(not directory.is_symlink(), "Refusing symbolic evidence directory.")
    save_json(directory / f"{name}.json", value)


def activity_evidence(state: dict, start: str, end: str, *,
                      assignment_id: str | None = None,
                      principal_id: str | None = None) -> dict:
    assignment = assignment_id if assignment_id is not None else state["assignment"]["id"]
    expected_principal = principal_id if principal_id is not None else state["userObjectId"]
    raw = scoped(state, ["monitor", "activity-log", "list", "--resource-id", assignment,
                         "--start-time", start, "--end-time", end, "--max-events", "1000"])
    require(isinstance(raw, list), "Invalid Activity Log response.")
    result = {"write": None, "delete": None, "requestedPrincipalId": None,
              "roleDefinitionId": None}
    for event in raw:
        require(isinstance(event, dict), "Invalid Activity Log event.")
        if str(event.get("resourceId", "")).lower() != assignment.lower():
            continue
        operation = event.get("operationName", {}).get("value", "").lower()
        if operation not in ("microsoft.authorization/roleassignments/write",
                             "microsoft.authorization/roleassignments/delete"):
            continue
        timestamp = event.get("eventTimestamp")
        require(utc_time(start) <= utc_time(timestamp) <= utc_time(end),
                "Activity event lies outside the requested UTC window.")
        if event.get("status", {}).get("value", "").lower() == "succeeded":
            result[operation.rsplit("/", 1)[1]] = {
                "timeUtc": timestamp, "correlationId": event.get("correlationId"),
                "status": "Succeeded",
            }
        body = event.get("properties", {}).get("requestbody")
        if body and operation.endswith("/write"):
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError as error:
                    raise WorkshopError("Malformed Activity request body.") from error
            require(isinstance(body, dict), "Invalid Activity request body.")
            body = {key.lower(): value for key, value in body.items()}
            properties = body.get("properties")
            require(isinstance(properties, dict), "Missing Activity request properties.")
            properties = {key.lower(): value for key, value in properties.items()}
            require(str(properties.get("principalid", "")).lower() == expected_principal.lower()
                    and str(properties.get("roledefinitionid", "")).lower()
                    == role_definition(state).lower(), "Activity target or Reader role mismatch.")
            result["requestedPrincipalId"] = properties["principalid"]
            result["roleDefinitionId"] = properties["roledefinitionid"]
    return result


def function_evidence(state: dict, workspace_id: str, start: str, end: str) -> dict | None:
    outputs = state["outputs"]
    query = f"""
let received = AppTraces
| where TimeGenerated between (datetime({start}) .. datetime({end}))
| where AppRoleName =~ '{outputs["functionAppName"]}'
| extend payload = parse_json(Message), InvocationId = tostring(Properties.InvocationId)
| where tostring(payload.eventName) == 'RbacRoleAssignmentReceived'
| where tostring(payload.roleAssignmentId) =~ '{state["assignment"]["id"]}'
| project RoleAssignmentId=tostring(payload.roleAssignmentId), EventId=tostring(payload.eventId),
          InvocationId, OperationId, ReceivedAtUtc=TimeGenerated, IngestedAtUtc=ingestion_time();
received
| join kind=inner (
    AppRequests
    | where TimeGenerated between (datetime({start}) .. datetime({end}))
    | where AppRoleName =~ '{outputs["functionAppName"]}'
    | project RequestInvocationId=tostring(Properties.InvocationId), RequestOperationId=OperationId,
              RequestAtUtc=TimeGenerated, Success, DurationMs
) on $left.OperationId == $right.RequestOperationId
| where isnotempty(InvocationId) and InvocationId == RequestInvocationId
| project RoleAssignmentId, EventId, InvocationId, OperationId, ReceivedAtUtc, IngestedAtUtc,
          RequestAtUtc, Success, DurationMs
| order by ReceivedAtUtc asc
"""
    rows = scoped(state, ["monitor", "log-analytics", "query", "--workspace", workspace_id,
                          "--analytics-query", query, "--timespan", f"{start}/{end}"])
    require(isinstance(rows, list), "Invalid Log Analytics query response.")
    for row in rows:
        require(isinstance(row, dict), "Invalid Function log row.")
        if str(row.get("RoleAssignmentId", "")).lower() != state["assignment"]["id"].lower():
            continue
        success = row.get("Success")
        require(type(success) is bool or success in ("True", "False", "true", "false"),
                "Invalid Function success value.")
        if success is not True and success not in ("True", "true"):
            continue
        require(all(isinstance(row.get(key), str) and row[key]
                    for key in ("EventId", "InvocationId", "OperationId")),
                "Missing Function correlation identifiers.")
        for key in ("ReceivedAtUtc", "RequestAtUtc"):
            require(utc_time(start) <= utc_time(row.get(key)) <= utc_time(end),
                    "Function event lies outside the requested UTC window.")
        utc_time(row.get("IngestedAtUtc"))
        try:
            duration = float(row["DurationMs"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkshopError("Invalid Function duration.") from error
        require(math.isfinite(duration) and duration >= 0, "Invalid Function duration.")
        return {"eventId": row["EventId"], "invocationId": row["InvocationId"],
                "operationId": row["OperationId"], "receivedAtUtc": row["ReceivedAtUtc"],
                "ingestedAtUtc": row["IngestedAtUtc"], "requestAtUtc": row["RequestAtUtc"],
                "durationMs": duration}
    return None


def metrics_evidence(state: dict, start: str, end: str) -> dict:
    topic = group_scope(state) + "/providers/Microsoft.EventGrid/systemTopics/" + state["outputs"]["systemTopicName"]
    raw = scoped(state, ["monitor", "metrics", "list", "--resource", topic,
                         "--metric", *METRICS, "--aggregation", "Total", "--interval", "PT1M",
                         "--start-time", start, "--end-time", end])
    require(isinstance(raw, dict) and isinstance(raw.get("timespan"), str),
            "Missing metrics timespan.")
    span = raw["timespan"].split("/")
    require(len(span) == 2 and utc_time(span[0]) == utc_time(start)
            and utc_time(span[1]) == utc_time(end), "Metrics timespan differs from requested UTC.")
    require(isinstance(raw.get("value"), list), "Invalid metrics values.")
    result = {}
    for metric in raw["value"]:
        require(isinstance(metric, dict), "Malformed metric result.")
        name = metric.get("name", {}).get("value")
        require(name in METRICS and name not in result, "Unexpected or duplicate metric.")
        error_code = metric.get("errorCode")
        require(error_code is None or (
            isinstance(error_code, str) and error_code.lower() == "success"
        ), f"Metric {name} query failed: {error_code}")
        samples = []
        require(isinstance(metric.get("timeseries"), list), "Missing metric timeseries.")
        for series in metric["timeseries"]:
            require(isinstance(series.get("data"), list), "Missing metric samples.")
            for item in series["data"]:
                total = item.get("total")
                if total is not None:
                    require(type(total) in (int, float) and math.isfinite(total) and total >= 0,
                            "Invalid metric total.")
                    samples.append(total)
        result[name] = sum(samples) if samples else None
    require(set(result) == set(METRICS), "Requested metrics are missing.")
    return result


def verify_event(state_file: Path, timeout_seconds: int = 600, interval_seconds: int = 20) -> dict:
    require(type(timeout_seconds) is int and timeout_seconds > 0
            and type(interval_seconds) is int and interval_seconds > 0, "Invalid polling interval.")
    state = load_state(state_file)
    check_run(state)
    require(owned_group(state) and bool(state["outputs"]) and state["assignment"] is not None,
            "Infrastructure or recorded test is missing.")
    outputs = state["outputs"]
    workspace = arm_get(state, outputs["workspaceResourceId"], "2023-09-01")
    require(workspace.get("id", "").lower() == outputs["workspaceResourceId"].lower(),
            "Foreign workspace response.")
    workspace_id = uuid_value(workspace.get("properties", {}).get("customerId"))
    start = (utc_time(state["assignment"]["requestedAtUtc"]) - timedelta(minutes=1)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    deadline = time.monotonic() + timeout_seconds
    while True:
        end = utc_time(now_utc()).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        require(utc_time(end) >= utc_time(start), "Recorded test is in the future.")
        activity = activity_evidence(state, start, end)
        function = function_evidence(state, workspace_id, start, end)
        verified = bool(activity["write"] and activity["delete"]
                        and activity["requestedPrincipalId"] and function)
        summary = {"verified": False, "assignmentId": state["assignment"]["id"],
                   "window": {"startUtc": start, "endUtc": end},
                   "activity": activity, "function": function,
                   "metricsScope": group_scope(state) + "/providers/Microsoft.EventGrid/systemTopics/"
                   + outputs["systemTopicName"], "metrics": None}
        write_evidence(state_file, state, "verification", summary)
        if verified:
            summary["metrics"] = metrics_evidence(state, start, end)
            summary["verified"] = True
            write_evidence(state_file, state, "verification", summary)
            return summary
        require(time.monotonic() < deadline,
                "Timeout waiting for correlated Activity and successful Function logs. "
                "Inspect the private verification.json; do not generate another assignment.")
        time.sleep(min(interval_seconds, max(0, deadline - time.monotonic())))


def cleanup_run(state_file: Path, confirmation: str | None, dry_run: bool = False) -> dict:
    state = load_state(state_file)
    require(check_identity(state) == state["userObjectId"].lower(), "Wrong signed-in user.")
    present = owned_group(state)
    phrase = f"DELETE {state['runId']} FROM {state['subscriptionId']}"
    assignment = state["assignment"]
    reader_id = state["outputs"].get("subscriptionReaderAssignmentId", "")
    roles = role_assignments(state) if assignment is not None or reader_id else []
    if assignment is not None:
        role = find_role(roles, assignment["id"])
        if role is not None:
            validate_role(state, role, assignment["id"], state["userObjectId"],
                          group_scope(state), "User")
    reader = find_role(roles, reader_id) if reader_id else None
    if reader is not None:
        outputs = state["outputs"]
        validate_role(state, reader, reader_id, outputs["managedIdentityPrincipalId"],
                      subscription_scope(state), "ServicePrincipal")
        if present:
            app = arm_get(state, outputs["functionAppResourceId"], "2024-11-01")
            require(app.get("id", "").lower() == outputs["functionAppResourceId"].lower()
                    and app.get("identity", {}).get("principalId", "").lower()
                    == outputs["managedIdentityPrincipalId"].lower(), "Foreign Function identity.")
        else:
            deployment = scoped(state, ["deployment", "sub", "show", "--name",
                                        state["deploymentNames"]["infra"]])
            recorded = deployment.get("properties", {}).get("outputs", {})
            require({key: value.get("value") for key, value in recorded.items()} == outputs,
                    "Cannot prove the recorded Reader belongs to this deployment.")
    result = {"confirmation": phrase, "resourceGroupId": group_scope(state),
              "resourceGroupRemoved": not present,
              "testAssignmentId": assignment["id"] if assignment else None,
              "subscriptionReader": "recorded" if reader_id else
              ("disabled" if state["outputs"] else "not-recorded")}
    if dry_run:
        return result
    require(confirmation == phrase, "Confirmation must exactly match the dry-run phrase.")
    if assignment is not None:
        remove_test_role(state_file, state)
    if reader is not None:
        delete_role(state, reader_id)
    if reader_id:
        result["subscriptionReader"] = "removed"
    if present:
        require(owned_group(state), "Group disappeared before deletion; rerun cleanup.")
        scoped(state, ["group", "delete", "--name", state["resourceGroupName"],
                       "--yes", "--no-wait"], allow_empty=True)
        wait_absent(lambda: owned_group(state), state["resourceGroupName"])
    result["resourceGroupRemoved"] = True
    write_evidence(state_file, state, "cleanup", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="RBAC event workshop")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "check", "capture", "package", "test-event", "verify", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("state", type=Path)
        if name == "capture":
            command.add_argument("--deployment", required=True)
        elif name == "verify":
            command.add_argument("--timeout-seconds", type=int, default=600)
        elif name == "cleanup":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--confirmation")
    init = commands.add_parser("init")
    init.add_argument("--subscription", required=True)
    init.add_argument("--tenant", required=True)
    init.add_argument("--location", default="koreacentral")
    args = parser.parse_args()
    try:
        if args.command == "init":
            print(init_run(Path.cwd(), args.subscription, args.tenant, args.location))
        else:
            state = load_state(args.state)
            if args.command == "check":
                check_run(state)
                print("PASS: run identity and ownership checked.")
            elif args.command == "capture":
                capture_deployment(args.state, args.deployment)
                print("PASS: deployment outputs captured.")
            elif args.command == "package":
                destination = args.state.parent / state["runId"] / "receiver.zip"
                package_sources(Path(__file__).resolve().parents[1], destination)
                print(destination)
            elif args.command == "test-event":
                print(json.dumps(exercise_event(args.state)))
            elif args.command == "verify":
                print(json.dumps(verify_event(args.state, args.timeout_seconds)))
            elif args.command == "cleanup":
                print(json.dumps(cleanup_run(args.state, args.confirmation, args.dry_run)))
            else:
                print(json.dumps(state, ensure_ascii=False))
    except (WorkshopError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
