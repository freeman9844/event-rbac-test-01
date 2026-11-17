"""Opt-in companyName experiment; operator writes are never deployed to the Function."""

import argparse
import json
import math
import re
import secrets
import sys
from datetime import timedelta
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import quote, urlsplit
from uuid import uuid4

import requests

from scripts import workshop as ws


GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
GLOBAL_ADMIN = "62e90394-69f5-4237-9190-012177145e10"
USER_ADMIN = "fe930be7-5e62-47db-91af-98c3a49a38b1"
PRIVILEGED_ADMIN = "e8611ab8-c189-46e8-94e1-60213ab1f814"
USER_SELECT = "id,userPrincipalName,displayName,mailNickname,accountEnabled,companyName,createdDateTime"


class OperatorClient:
    def __init__(self, state: dict):
        self.state = state

    def request(self, method: str, path: str, *, body: dict | None = None,
                params: dict | None = None, missing_ok: bool = False,
                arm: bool = False) -> dict | None:
        host = "https://management.azure.com" if arm else "https://graph.microsoft.com"
        ws.require(path.startswith("/") and not path.startswith("//")
                   and not urlsplit(path).scheme and not urlsplit(path).netloc,
                   "Unsafe API path.")
        token = ws.scoped(self.state, [
            "account", "get-access-token", "--tenant", self.state["tenantId"],
            "--resource", host,
        ])
        ws.require(isinstance(token, dict) and token.get("tenant", "").lower()
                   == self.state["tenantId"].lower()
                   and isinstance(token.get("accessToken"), str) and token["accessToken"],
                   "Wrong token tenant or missing credential.")
        try:
            result = requests.request(
                method, host + path, params=params, json=body,
                headers={"Authorization": "Bearer " + token["accessToken"]},
                timeout=(5, 30), allow_redirects=False,
            )
        except requests.RequestException:
            raise ws.WorkshopError(f"{method} transport failed; reconcile recorded intent.") from None
        if result.status_code == 404 and missing_ok:
            return None
        ws.require(result.status_code in (200, 201, 204),
                   f"{method} API failed: HTTP {result.status_code}.")
        if result.status_code == 204:
            return None
        try:
            value = result.json()
        except ValueError:
            raise ws.WorkshopError("API returned invalid JSON.") from None
        ws.require(isinstance(value, dict), "API returned a non-object response.")
        return value

    def items(self, path: str, params: dict | None = None) -> list[dict]:
        endpoint = urlsplit(path).path
        visited = set()
        values = []
        while path:
            ws.require(path not in visited, "Repeated Graph pagination URL.")
            visited.add(path)
            page = self.request("GET", path, params=params)
            ws.require(isinstance(page, dict) and isinstance(page.get("value"), list)
                       and all(isinstance(item, dict) for item in page["value"]),
                       "Invalid Graph collection.")
            values.extend(page["value"])
            link = page.get("@odata.nextLink")
            if not link:
                break
            ws.require(isinstance(link, str), "Invalid Graph nextLink.")
            parsed = urlsplit(link)
            ws.require(parsed.scheme == "https" and parsed.netloc == "graph.microsoft.com"
                       and parsed.path == endpoint and not parsed.fragment,
                       "Unsafe Graph pagination URL.")
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            params = None
        return values


def _graph_role(client: OperatorClient) -> tuple[str, str]:
    graph = client.request("GET", f"/v1.0/servicePrincipals(appId='{GRAPH_APP_ID}')",
                           params={"$select": "id,appId,appRoles"})
    ws.require(isinstance(graph, dict) and graph.get("appId") == GRAPH_APP_ID
               and isinstance(graph.get("appRoles"), list), "Invalid Microsoft Graph principal.")
    roles = [role for role in graph["appRoles"] if isinstance(role, dict)
             and role.get("value") == "User.Read.All" and role.get("isEnabled") is True
             and "Application" in role.get("allowedMemberTypes", [])]
    ws.require(len(roles) == 1, "Cannot identify Graph User.Read.All application permission.")
    return ws.uuid_value(graph["id"]), ws.uuid_value(roles[0].get("id"))


def _preflight(base: dict, client: OperatorClient) -> dict:
    me = client.request("GET", "/v1.0/me", params={"$select": "id"})
    ws.require(isinstance(me, dict) and ws.uuid_value(me.get("id")) == base["userObjectId"],
               "Graph caller is not this run's operator.")
    roles = {role.get("roleTemplateId") for role in client.items(
        "/v1.0/me/transitiveMemberOf/microsoft.graph.directoryRole",
        {"$select": "roleTemplateId"})}
    ws.require(GLOBAL_ADMIN in roles or {USER_ADMIN, PRIVILEGED_ADMIN} <= roles,
               "Entra administrator required: User Administrator + Privileged Role "
               "Administrator, or an already authorized Global Administrator.")
    organizations = client.items("/v1.0/organization", {"$select": "id,verifiedDomains"})
    ws.require(len(organizations) == 1
               and organizations[0].get("id", "").lower() == base["tenantId"],
               "Wrong Graph organization.")
    domains = organizations[0].get("verifiedDomains")
    ws.require(isinstance(domains, list), "Missing verified domains.")
    initial = [domain["name"] for domain in domains if isinstance(domain, dict)
               and domain.get("isInitial") is True and domain.get("type") == "Managed"
               and isinstance(domain.get("name"), str)
               and re.fullmatch(r"[a-zA-Z0-9.-]+", domain["name"])]
    ws.require(len(initial) == 1, "A verified initial Managed domain is required.")
    resource_id, role_id = _graph_role(client)
    return {"ready": True, "domain": initial[0],
            "graphResourceId": resource_id, "graphAppRoleId": role_id}


def preflight(state_file: Path) -> dict:
    base = ws.load_state(state_file)
    ws.check_run(base)
    return _preflight(base, OperatorClient(base))


def _path(state_file: Path, base: dict) -> Path:
    return state_file.with_name(base["runId"] + "-companyname.json")


def _load(state_file: Path, base: dict) -> dict:
    try:
        exp = json.loads(_path(state_file, base).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ws.WorkshopError("Missing or unreadable companyName state; do not guess targets.") from None
    expected = {"schemaVersion", "runId", "subscriptionId", "tenantId", "operatorObjectId",
                "companyName", "user", "grant", "assignment"}
    ws.require(isinstance(exp, dict) and set(exp) == expected
               and type(exp["schemaVersion"]) is int and exp["schemaVersion"] == 1,
               "Invalid companyName state.")
    for key in ("runId", "subscriptionId", "tenantId"):
        ws.require(exp[key] == base[key], "Foreign experiment state.")
    ws.require(exp["operatorObjectId"] == base["userObjectId"], "Foreign operator.")
    ws.require(isinstance(exp["companyName"], str) and exp["companyName"].strip()
               and len(exp["companyName"]) <= 64, "Invalid recorded companyName.")
    user, grant = exp["user"], exp["grant"]
    ws.require(isinstance(user, dict) and set(user) == {"id", "upn", "attempted", "removed"}
               and isinstance(user["upn"], str)
               and re.fullmatch(r"rbac-companyname-" + base["runId"] + r"@[a-zA-Z0-9.-]+",
                                user["upn"]) is not None, "Foreign test user.")
    if user["id"] is not None:
        ws.uuid_value(user["id"])
        ws.require(user["id"] != base["userObjectId"], "Operator cannot be a test user.")
    ws.require(isinstance(grant, dict) and set(grant) == {
        "id", "principalId", "resourceId", "appRoleId", "attempted", "existing", "removed",
    }, "Invalid Graph grant state.")
    ws.require(grant["principalId"] == base["outputs"].get("managedIdentityPrincipalId"),
               "Foreign managed identity.")
    for key in ("principalId", "resourceId", "appRoleId"):
        ws.uuid_value(grant[key])
    ws.require(grant["id"] is None or (
        isinstance(grant["id"], str)
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,256}={0,2}", grant["id"]) is not None),
        "Invalid Graph assignment ID.")
    for record, keys in ((user, ("attempted", "removed")),
                         (grant, ("attempted", "existing", "removed"))):
        ws.require(all(type(record[key]) is bool for key in keys), "Invalid lifecycle flags.")
    assignment = exp["assignment"]
    if assignment is not None:
        ws.require(isinstance(assignment, dict) and set(assignment) == {
            "id", "name", "requestedAtUtc", "created", "removed",
        }, "Invalid advanced test assignment.")
        name = ws.uuid_value(assignment["name"])
        ws.require(assignment["id"] == ws.group_scope(base)
                   + "/providers/Microsoft.Authorization/roleAssignments/" + name
                   and type(assignment["created"]) is bool
                   and type(assignment["removed"]) is bool
                   and ws.utc_time(assignment["requestedAtUtc"]) >= ws.utc_time(base["createdAtUtc"]),
                   "Foreign advanced test assignment.")
    return exp


def _save(state_file: Path, base: dict, exp: dict) -> None:
    ws.save_json(_path(state_file, base), exp)


def _owned_user(base: dict, exp: dict, client: OperatorClient) -> dict | None:
    record = exp["user"]
    key = record["id"] or record["upn"]
    user = client.request("GET", "/v1.0/users/" + quote(key, safe=""),
                          params={"$select": USER_SELECT}, missing_ok=True)
    if user is None:
        return None
    ws.require(isinstance(user, dict) and record["attempted"]
               and user.get("userPrincipalName", "").lower() == record["upn"].lower()
               and user.get("mailNickname") == "rbac-companyname-" + base["runId"]
               and user.get("displayName") == "RBAC companyName " + base["runId"]
               and user.get("accountEnabled") is False
               and ws.utc_time(user.get("createdDateTime")) >= ws.utc_time(base["createdAtUtc"]),
               "User ownership mismatch; refusing deletion.")
    user_id = ws.uuid_value(user.get("id"))
    ws.require(user_id != base["userObjectId"] and
               (record["id"] is None or record["id"] == user_id), "Foreign user identity.")
    record["id"] = user_id
    return user


def _grant(base: dict, exp: dict, client: OperatorClient) -> dict | None:
    record = exp["grant"]
    resource, role = _graph_role(client)
    ws.require(record["resourceId"] == resource and record["appRoleId"] == role,
               "Foreign Graph application permission.")
    grants = client.items("/v1.0/servicePrincipals/" + record["principalId"] + "/appRoleAssignments")
    matches = [item for item in grants if (
        item.get("id") == record["id"] if record["id"] is not None else
        all(item.get(key) == record[key] for key in ("principalId", "resourceId", "appRoleId")))]
    ws.require(len(matches) <= 1, "Ambiguous Graph assignment.")
    if matches:
        ws.require(all(matches[0].get(key) == record[key]
                       for key in ("principalId", "resourceId", "appRoleId")),
                   "Graph assignment ownership mismatch.")
        return matches[0]
    return None


def prepare(state_file: Path, company_name: str) -> dict:
    ws.require(isinstance(company_name, str) and company_name.strip()
               and len(company_name) <= 64, "companyName must contain 1-64 characters.")
    base = ws.load_state(state_file)
    ws.check_run(base)
    ws.require(ws.owned_group(base) and base["outputs"], "Capture infrastructure first.")
    ws.require(not _path(state_file, base).exists(), "Experiment already recorded; clean that run.")
    client = OperatorClient(base)
    ready = _preflight(base, client)
    app = ws.arm_get(base, base["outputs"]["functionAppResourceId"], "2024-11-01")
    ws.require(app.get("id", "").lower() == base["outputs"]["functionAppResourceId"].lower()
               and app.get("identity", {}).get("principalId", "").lower()
               == base["outputs"]["managedIdentityPrincipalId"], "Foreign Function identity.")
    reader_id = base["outputs"].get("subscriptionReaderAssignmentId")
    ws.require(reader_id, "Capture the opt-in Function subscription Reader first.")
    reader = ws.find_role(ws.role_assignments(base), reader_id)
    ws.require(reader is not None, "Function subscription Reader is missing.")
    ws.validate_role(base, reader, reader_id, base["outputs"]["managedIdentityPrincipalId"],
                     ws.subscription_scope(base), "ServicePrincipal")
    nickname = "rbac-companyname-" + base["runId"]
    upn = nickname + "@" + ready["domain"]
    ws.require(client.request("GET", "/v1.0/users/" + quote(upn, safe=""),
                              params={"$select": "id"}, missing_ok=True) is None,
               "A user with this run name already exists; it will not be adopted.")
    exp = {
        "schemaVersion": 1, "runId": base["runId"], "subscriptionId": base["subscriptionId"],
        "tenantId": base["tenantId"], "operatorObjectId": base["userObjectId"],
        "companyName": company_name, "user": {
            "id": None, "upn": upn, "attempted": False, "removed": False,
        }, "grant": {
            "id": None, "principalId": base["outputs"]["managedIdentityPrincipalId"],
            "resourceId": ready["graphResourceId"], "appRoleId": ready["graphAppRoleId"],
            "attempted": False, "existing": False, "removed": False,
        }, "assignment": None,
    }
    previous = _grant(base, exp, client)
    if previous:
        exp["grant"].update(id=previous["id"], existing=True)
    _save(state_file, base, exp)
    try:
        exp["user"]["attempted"] = True
        _save(state_file, base, exp)
        created = client.request("POST", "/v1.0/users", body={
            "accountEnabled": False, "displayName": "RBAC companyName " + base["runId"],
            "mailNickname": nickname, "userPrincipalName": upn, "companyName": company_name,
            "passwordProfile": {"forceChangePasswordNextSignIn": True,
                                "password": secrets.token_urlsafe(32) + "aA1!"},
        })
        ws.require(isinstance(created, dict), "Missing user creation response.")
        exp["user"]["id"] = ws.uuid_value(created.get("id"))
        _save(state_file, base, exp)
        user = _owned_user(base, exp, client)
        ws.require(user is not None and user.get("companyName") == company_name,
                   "Created user's companyName does not match.")
        if previous is None:
            exp["grant"]["attempted"] = True
            _save(state_file, base, exp)
            granted = client.request("POST", "/v1.0/servicePrincipals/"
                                     + ready["graphResourceId"] + "/appRoleAssignedTo",
                                     body={key: exp["grant"][key] for key in
                                           ("principalId", "resourceId", "appRoleId")})
            ws.require(isinstance(granted, dict) and isinstance(granted.get("id"), str),
                       "Missing Graph permission response.")
            exp["grant"]["id"] = granted["id"]
            _save(state_file, base, exp)
        ws.require(_grant(base, exp, client) is not None, "Graph permission not confirmed.")
        _save(state_file, base, exp)
        return {"prepared": True, "userId": exp["user"]["id"], "companyName": company_name}
    except (ws.WorkshopError, OSError, KeyboardInterrupt) as error:
        try:
            cleanup(state_file)
        except (ws.WorkshopError, OSError) as cleanup_error:
            raise ws.WorkshopError(f"{error}; cleanup also failed: {cleanup_error}") from None
        raise


def cleanup(state_file: Path) -> dict:
    base = ws.load_state(state_file)
    ws.check_run(base)
    exp = _load(state_file, base)
    client = OperatorClient(base)
    errors = []
    assignment = exp["assignment"]
    if assignment is not None:
        try:
            role = ws.find_role(ws.role_assignments(base), assignment["id"])
            if role is not None:
                ws.validate_role(base, role, assignment["id"], exp["user"]["id"],
                                 ws.group_scope(base), "User")
                ws.delete_role(base, assignment["id"])
            assignment["removed"] = True
            _save(state_file, base, exp)
        except (ws.WorkshopError, OSError) as error:
            errors.append(f"testReader: {error}")
    try:
        grant = _grant(base, exp, client)
        if not exp["grant"]["existing"]:
            ws.require(grant is None or exp["grant"]["attempted"],
                       "Unrecorded Graph permission; refusing adoption.")
            if grant is not None:
                exp["grant"]["id"] = grant["id"]
                _save(state_file, base, exp)
                client.request("DELETE", "/v1.0/servicePrincipals/"
                               + exp["grant"]["resourceId"] + "/appRoleAssignedTo/"
                               + quote(grant["id"], safe=""), missing_ok=True)
            ws.wait_absent(lambda: _grant(base, exp, client) is not None, "Graph permission")
            exp["grant"]["removed"] = True
            _save(state_file, base, exp)
    except (ws.WorkshopError, OSError) as error:
        errors.append(f"graphPermission: {error}")
    try:
        user = _owned_user(base, exp, client)
        if user is not None:
            _save(state_file, base, exp)
            client.request("DELETE", "/v1.0/users/" + user["id"], missing_ok=True)
        ws.wait_absent(lambda: _owned_user(base, exp, client) is not None, "test user")
        exp["user"]["removed"] = True
        _save(state_file, base, exp)
    except (ws.WorkshopError, OSError) as error:
        errors.append(f"testUser: {error}")
    report = {"complete": not errors, "testReaderRemoved":
              assignment["removed"] if assignment is not None else True,
              "userRemoved": exp["user"]["removed"],
              "graphPermission": "preservedExisting" if exp["grant"]["existing"] else
              ("removed" if exp["grant"]["removed"] else "remaining"), "errors": errors}
    ws.write_evidence(state_file, base, "companyname-cleanup", report)
    ws.require(not errors, "CompanyName cleanup incomplete: " + "; ".join(errors))
    return report


def _observation(base: dict, exp: dict, workspace_id: str, start: str, end: str) -> dict | None:
    query = f"""
let observed = AppTraces
| where TimeGenerated between (datetime({start}) .. datetime({end}))
| where AppRoleName =~ '{base["outputs"]["functionAppName"]}'
| extend payload=parse_json(Message), InvocationId=tostring(Properties.InvocationId)
| where tostring(payload.eventName) == 'RbacRoleAssignmentObserved'
| where tostring(payload.roleAssignmentId) =~ '{exp["assignment"]["id"]}'
| project EventName=tostring(payload.eventName), RoleAssignmentId=tostring(payload.roleAssignmentId),
    PrincipalId=tostring(payload.principalId), PrincipalType=tostring(payload.principalType),
    RoleDefinitionId=tostring(payload.roleDefinitionId), RoleName=tostring(payload.roleName),
    Scope=tostring(payload.scope), CompanyName=tostring(payload.companyName),
    EnrichmentStatus=tostring(payload.enrichmentStatus), EventId=tostring(payload.eventId),
    InvocationId, OperationId, ObservedAtUtc=TimeGenerated, IngestedAtUtc=ingestion_time(),
    LookupAtUtc=tostring(payload.observedAt);
observed
| join kind=inner (
    AppRequests
    | where TimeGenerated between (datetime({start}) .. datetime({end}))
    | where AppRoleName =~ '{base["outputs"]["functionAppName"]}'
    | project RequestInvocationId=tostring(Properties.InvocationId),
        RequestOperationId=OperationId, RequestAtUtc=TimeGenerated, Success, DurationMs
) on $left.OperationId == $right.RequestOperationId
| where isnotempty(InvocationId) and InvocationId == RequestInvocationId
| order by ObservedAtUtc asc
"""
    rows = ws.scoped(base, ["monitor", "log-analytics", "query", "--workspace", workspace_id,
                            "--analytics-query", " ".join(query.splitlines()),
                            "--timespan", f"{start}/{end}"])
    ws.require(isinstance(rows, list), "Invalid observation query result.")
    for row in rows:
        ws.require(isinstance(row, dict), "Invalid observation row.")
        expected = {
            "EventName": "RbacRoleAssignmentObserved",
            "RoleAssignmentId": exp["assignment"]["id"], "PrincipalId": exp["user"]["id"],
            "PrincipalType": "User", "RoleDefinitionId": ws.role_definition(base),
            "RoleName": "Reader", "Scope": ws.group_scope(base),
        }
        ws.require(all(isinstance(row.get(key), str) and row[key].lower() == value.lower()
                       for key, value in expected.items())
                   and row.get("CompanyName") == exp["companyName"]
                   and row.get("EnrichmentStatus") == "succeeded",
                   "Observed principal, companyName, role or scope does not match.")
        ws.require(all(isinstance(row.get(key), str) and row[key]
                       for key in ("EventId", "InvocationId", "OperationId"))
                   and row["InvocationId"] == row.get("RequestInvocationId")
                   and row["OperationId"] == row.get("RequestOperationId")
                   and (row.get("Success") is True or row.get("Success") in ("True", "true")),
                   "Observation is not correlated with the same successful Function request.")
        for key in ("ObservedAtUtc", "LookupAtUtc", "RequestAtUtc"):
            ws.require(ws.utc_time(start) <= ws.utc_time(row.get(key)) <= ws.utc_time(end),
                       "Observation lies outside the requested UTC window.")
        ws.utc_time(row.get("IngestedAtUtc"))
        try:
            duration = float(row["DurationMs"])
        except (KeyError, ValueError, TypeError):
            raise ws.WorkshopError("Invalid observed Function duration.") from None
        ws.require(math.isfinite(duration) and duration >= 0, "Invalid Function duration.")
        return {
            "eventId": row["EventId"], "invocationId": row["InvocationId"],
            "operationId": row["OperationId"], "principalId": row["PrincipalId"],
            "roleName": row["RoleName"], "roleDefinitionId": row["RoleDefinitionId"],
            "scope": row["Scope"], "companyName": row["CompanyName"],
            "enrichmentStatus": row["EnrichmentStatus"], "observedAtUtc": row["ObservedAtUtc"],
            "lookupAtUtc": row["LookupAtUtc"], "ingestedAtUtc": row["IngestedAtUtc"],
            "requestAtUtc": row["RequestAtUtc"], "durationMs": duration,
        }
    return None


def exercise(state_file: Path, timeout_seconds: int = 900) -> dict:
    ws.require(type(timeout_seconds) is int and timeout_seconds > 0, "Invalid timeout.")
    base = ws.load_state(state_file)
    ws.check_run(base)
    exp = _load(state_file, base)
    ws.require(exp["assignment"] is None, "An advanced test is already recorded; do not repeat it.")
    failure = None
    result = None
    try:
        ws.require(ws.owned_group(base), "Experiment group is missing.")
        client = OperatorClient(base)
        user = _owned_user(base, exp, client)
        ws.require(user is not None and user.get("companyName") == exp["companyName"],
                   "Prepared test user or companyName is missing.")
        ws.require(_grant(base, exp, client) is not None, "Prepared Graph permission is missing.")
        for role in ws.role_assignments(base):
            props = role["properties"]
            ws.require(not (
                str(props.get("scope", "")).lower() == ws.group_scope(base).lower()
                and str(props.get("principalId", "")).lower() == exp["user"]["id"]
                and str(props.get("roleDefinitionId", "")).lower() == ws.role_definition(base).lower()
            ), "Existing test user Reader will not be replaced.")
        name = str(uuid4())
        assignment = {
            "name": name, "id": ws.group_scope(base)
            + "/providers/Microsoft.Authorization/roleAssignments/" + name,
            "requestedAtUtc": ws.now_utc(), "created": False, "removed": False,
        }
        exp["assignment"] = assignment
        _save(state_file, base, exp)
        role = client.request("PUT", assignment["id"], arm=True,
                              params={"api-version": ws.ROLE_API}, body={"properties": {
                                  "principalId": exp["user"]["id"], "principalType": "User",
                                  "roleDefinitionId": ws.role_definition(base),
                              }})
        ws.require(isinstance(role, dict), "Missing test Reader response.")
        ws.validate_role(base, role, assignment["id"], exp["user"]["id"],
                         ws.group_scope(base), "User")
        assignment["created"] = True
        _save(state_file, base, exp)
        workspace = ws.arm_get(base, base["outputs"]["workspaceResourceId"], "2023-09-01")
        ws.require(workspace.get("id", "").lower()
                   == base["outputs"]["workspaceResourceId"].lower(), "Foreign workspace.")
        workspace_id = ws.uuid_value(workspace.get("properties", {}).get("customerId"))
        start = (ws.utc_time(assignment["requestedAtUtc"]) - timedelta(minutes=1)).replace(
            microsecond=0).isoformat().replace("+00:00", "Z")
        deadline = monotonic() + timeout_seconds
        while True:
            end = ws.utc_time(ws.now_utc()).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            activity = ws.activity_evidence(
                base, start, end, assignment_id=assignment["id"], principal_id=exp["user"]["id"])
            function = _observation(base, exp, workspace_id, start, end)
            verified = bool(activity["write"] and activity["requestedPrincipalId"] and function)
            result = {
                "verified": verified, "assignmentId": assignment["id"],
                "expectedCompanyName": exp["companyName"], "activity": activity,
                "function": function, "window": {"startUtc": start, "endUtc": end},
            }
            ws.write_evidence(state_file, base, "companyname-verification", result)
            if verified:
                break
            ws.require(monotonic() < deadline,
                       "Timed out waiting for companyName observation; no new assignment was made.")
            sleep(min(20, max(0, deadline - monotonic())))
    except (ws.WorkshopError, OSError, KeyboardInterrupt) as error:
        failure = error
    finally:
        try:
            cleanup(state_file)
        except (ws.WorkshopError, OSError) as error:
            if failure is not None:
                raise ws.WorkshopError(f"{failure}; cleanup also failed: {error}") from None
            raise
    if failure is not None:
        raise failure
    ws.require(result is not None and result["verified"], "No verified observation.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in companyName RBAC experiment")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "prepare", "exercise", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("state", type=Path)
        if name == "prepare":
            command.add_argument("--company-name", required=True)
        elif name == "exercise":
            command.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            value = prepare(args.state, args.company_name)
        elif args.command == "exercise":
            value = exercise(args.state, args.timeout_seconds)
        elif args.command == "cleanup":
            value = cleanup(args.state)
        else:
            value = preflight(args.state)
        print(json.dumps(value))
    except (ws.WorkshopError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
