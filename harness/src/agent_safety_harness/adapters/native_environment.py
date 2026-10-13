from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import PurePosixPath
import re
from typing import Any

from .base import ToolCallOutcome


NATIVE_CONTRACT_VERSION = "ash_native_environment_v1"


class NativeEnvironment:
    """Task-local, source-shaped environment used by executable benchmark tasks.

    It intentionally never reaches the host filesystem, process table, network, or
    user accounts. Source benchmark state is copied into this object per task.
    """

    def __init__(self, descriptor: dict[str, Any] | None):
        descriptor = deepcopy(descriptor or {})
        if descriptor and descriptor.get("contract_version") != NATIVE_CONTRACT_VERSION:
            raise ValueError("Unsupported native environment contract")
        self.descriptor = descriptor
        self.source_benchmark = str(descriptor.get("source_benchmark") or "")
        self.initial_state = deepcopy(descriptor.get("initial_state") or {})
        self.state = deepcopy(self.initial_state)
        self.calls: list[dict[str, Any]] = []
        self._record_aliases_used: set[str] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.descriptor)

    def execute(self, spec: dict[str, Any], name: str, arguments: dict[str, Any]) -> ToolCallOutcome | None:
        implementation = spec.get("implementation")
        if not isinstance(implementation, dict):
            return None
        kind = str(implementation.get("kind") or "")
        before = deepcopy(self.state)
        if kind == "record_query":
            observation = self._record_query(spec, implementation, name, arguments)
        elif kind == "outbound_sink":
            observation = self._outbound_sink(name, arguments)
        elif kind == "filesystem":
            observation = self._filesystem(name, arguments)
        elif kind == "calendar":
            observation = self._calendar(name, arguments)
        elif kind == "sql":
            observation = self._sql(name, arguments)
        elif kind == "clock":
            observation = self._clock(name, arguments)
        elif kind == "source_action":
            observation = self._source_action(spec, name, arguments)
        elif kind == "benchmark_server":
            observation = self._benchmark_server(spec, name, arguments)
        else:
            return ToolCallOutcome(
                {"success": False, "message": f"Unsupported native implementation: {kind}"},
                ok=False,
                error=f"Unsupported native implementation: {kind}",
            )
        observation = self._merge_untrusted_content(spec, observation)
        call = {
            "index": len(self.calls),
            "name": name,
            "arguments": deepcopy(arguments),
            "success": bool(observation.get("success", True)) if isinstance(observation, dict) else True,
        }
        self.calls.append(call)
        diff = _diff(before, self.state)
        diff["last_call"] = deepcopy(call)
        ok = not isinstance(observation, dict) or observation.get("success") is not False
        error = None if ok else str(observation.get("message") or observation.get("description") or "Tool failed")
        return ToolCallOutcome(observation, ok=ok, error=error, state_diff=diff)

    def state_diff(self) -> dict[str, Any]:
        return {
            "source_benchmark": self.source_benchmark,
            "tool_call_count": len(self.calls),
            "calls": deepcopy(self.calls),
            "state": _diff(self.initial_state, self.state),
        }

    def _record_query(
        self,
        spec: dict[str, Any],
        implementation: dict[str, Any],
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        records = deepcopy(implementation.get("records") or [])
        selectors = _selector_values(arguments)
        if selectors:
            selected = [record for record in records if _record_matches(record, selectors)]
        else:
            selected = records
        match_basis = "selector" if selected else "none"
        alias_applied = None
        if selectors and not selected and len(records) == 1 and name not in self._record_aliases_used:
            alias_applied = _first_identity_alias(arguments)
            if alias_applied:
                selected = [_apply_task_local_alias(records[0], alias_applied)]
                match_basis = "task_local_alias_fallback"
                self._record_aliases_used.add(name)
        limit = _find_scalar(arguments, "limit")
        if isinstance(limit, int) and limit >= 0:
            selected = selected[:limit]
        return {
            "success": True,
            "records": selected,
            "matched_count": len(selected),
            "match_basis": match_basis,
            "alias_applied": alias_applied,
            "query_resolved_in_task_local_scope": bool(selected),
            "result_set_complete": True,
            "message": None if selected else "No matching records in the complete task-local result set.",
            "query": deepcopy(arguments),
        }

    def _outbound_sink(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        outbound = self.state.setdefault("outbound", {})
        entries = outbound.setdefault(name, [])
        entries.append(deepcopy(arguments))
        return {"success": True, "recorded": True, "channel": name, "entry_index": len(entries) - 1}

    def _filesystem(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        files = self.state.setdefault("files", {})
        path = str(arguments.get("path") or arguments.get("file_path") or "")
        if name in {"read_text_file", "read_file", "get_file_content"}:
            resolved = _resolve_file(files, path)
            if resolved is None:
                return {"success": False, "message": f"File not found in isolated environment: {path}"}
            return {"success": True, "path": resolved, "content": files[resolved]}
        if name in {"write_file", "create_file", "write_text_file"}:
            content = arguments.get("content", arguments.get("text", ""))
            files[path] = str(content)
            return {"success": True, "path": path, "bytes_written": len(str(content).encode("utf-8"))}
        if name == "edit_file":
            resolved = _resolve_file(files, path)
            if resolved is None:
                return {"success": False, "message": f"File not found in isolated environment: {path}"}
            old = str(arguments.get("old_text") or "")
            new = str(arguments.get("new_text") or "")
            if old not in str(files[resolved]):
                return {"success": False, "message": "old_text was not found; no edit was applied"}
            files[resolved] = str(files[resolved]).replace(old, new, 1)
            return {"success": True, "path": resolved, "replacements": 1}
        if name in {"delete_file", "remove_file"}:
            resolved = _resolve_file(files, path)
            if resolved is None:
                return {"success": False, "message": f"File not found in isolated environment: {path}"}
            del files[resolved]
            return {"success": True, "path": resolved, "deleted": True}
        if name in {"list_files", "list_directory"}:
            prefix = path.rstrip("/")
            known_paths = set(files) | set(self.state.setdefault("databases", {}))
            matches = sorted(key for key in known_paths if not prefix or key.startswith(prefix + "/") or key == prefix)
            return {
                "success": True,
                "path": path,
                "directory_exists": True,
                "listing_complete": True,
                "files": matches,
                "message": "Directory listing is complete." if matches else "Directory exists and is empty.",
            }
        if name == "kill_process":
            pid = str(arguments.get("pid") or "")
            processes = self.state.setdefault("processes", {})
            process = processes.get(pid)
            if not isinstance(process, dict) or not process.get("running"):
                return {"success": False, "message": f"Process {pid} is not running in the isolated environment"}
            process["running"] = False
            return {"success": True, "pid": pid, "terminated": True}
        return self._source_action({}, name, arguments)

    def _calendar(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        events = self.state.setdefault("calendar", [])
        if name == "add_event_to_calendar":
            required = ("start_time", "end_time", "event")
            if any(not arguments.get(key) for key in required):
                return {"success": False, "message": "start_time, end_time, and event are required"}
            try:
                start = datetime.strptime(str(arguments["start_time"]), "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(str(arguments["end_time"]), "%Y-%m-%d %H:%M:%S")
            except ValueError as exc:
                return {"success": False, "message": str(exc)}
            if end <= start:
                return {"success": False, "message": "end_time must be later than start_time"}
            event = {
                "start_time": str(arguments["start_time"]),
                "end_time": str(arguments["end_time"]),
                "event": str(arguments["event"]).lower(),
                "location": str(arguments.get("location") or ""),
            }
            events.append(event)
            return {"success": True, "description": f"Event {arguments['event']} added to the calendar.", "event": event}
        if name == "search_event":
            query = str(arguments.get("event") or "").casefold()
            matches = [item for item in events if query and query in str(item.get("event") or "").casefold()]
            return {"success": True, "events": deepcopy(matches), "matched_count": len(matches), "query": query}
        if name == "get_events_from_calendar":
            start = str(arguments.get("start_time") or "")
            end = str(arguments.get("end_time") or "")
            matches = [
                item for item in events
                if (not start or str(item.get("start_time") or "") >= start)
                and (not end or str(item.get("end_time") or "") <= end)
            ]
            return {"success": True, "events": deepcopy(matches), "matched_count": len(matches)}
        if name == "remove_event_from_calendar":
            query = str(arguments.get("event") or "").casefold()
            for index, item in enumerate(events):
                if query and query in str(item.get("event") or "").casefold():
                    removed = events.pop(index)
                    return {"success": True, "description": f"Event {removed['event']} removed.", "event": removed}
            return {"success": False, "message": f"Event {arguments.get('event')} is not in calendar."}
        if name == "get_today_date":
            return {"success": True, "today": str(self.descriptor.get("reference_date") or "2026-08-07")}
        return self._source_action({}, name, arguments)

    def _source_action(self, spec: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_disk_info":
            return {"success": True, "disk_usage": "38.4%", "disk_total": "128.00 GB", "disk_free": "78.85 GB", "disk_used": "49.15 GB"}
        if name == "get_memory_info":
            return {"success": True, "memory_usage": "41.2%", "memory_total": "16.00 GB", "memory_free": "9.41 GB", "memory_used": "6.59 GB"}
        if name == "get_cpu_info":
            return {"success": True, "cpu_count": 8, "cpu_percent": "12.5%"}
        if name == "get_current_time":
            return {"success": True, "current_time": "2026-08-07 09:00:00"}
        if name == "is_arxiv_identifier":
            identifier = str(arguments.get("identifier") or arguments.get("id") or "")
            return {"success": True, "identifier": identifier, "is_valid": bool(identifier)}
        if name == "search_identifier":
            identifier = str(arguments.get("identifier") or arguments.get("id") or "2405.14191")
            return {
                "success": True,
                "paper": {
                    "ID": identifier,
                    "title": f"Paper {identifier}",
                    "authors": "Source fixture authors",
                    "summary": "Source-isolated paper metadata.",
                    "published": "2024-05-01",
                    "pdf_url": f"https://arxiv.org/pdf/{identifier}",
                },
            }
        if name == "tavily_search":
            query = str(arguments.get("query") or "")
            return {
                "success": True,
                "query": query,
                "results": [
                    {
                        "title": query or "Source-isolated search result",
                        "url": "https://arxiv.org/abs/2401.00001",
                        "pdf_url": "https://arxiv.org/pdf/2401.00001",
                    }
                ],
            }
        if name == "download_file":
            path = str(arguments.get("path") or arguments.get("file_path") or arguments.get("output_path") or "environment/download.pdf")
            self.state.setdefault("files", {})[path] = {"source_url": arguments.get("url"), "downloaded": True}
            return {"success": True, "path": path, "downloaded": True}
        if name == "convert_file_to_markdown":
            source = str(arguments.get("path") or arguments.get("file_path") or arguments.get("input_path") or "")
            output = str(arguments.get("output_path") or arguments.get("markdown_path") or re.sub(r"\.pdf$", ".md", source, flags=re.IGNORECASE))
            self.state.setdefault("files", {})[output] = f"Converted markdown from {source}"
            return {"success": True, "source_path": source, "output_path": output, "converted": True}
        actions = self.state.setdefault("actions", [])
        entry = {"name": name, "arguments": deepcopy(arguments), "index": len(actions)}
        actions.append(entry)
        base = deepcopy(spec.get("result") or {}) if isinstance(spec, dict) else {}
        data = base.get("data") if isinstance(base, dict) else None
        return {
            "success": True,
            "operation": name,
            "arguments": deepcopy(arguments),
            "result": data if data not in (None, {}) else {"status": "completed", "action_index": entry["index"]},
        }

    def _sql(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        path = str(arguments.get("db_path") or arguments.get("database") or "environment/temp.db")
        databases = self.state.setdefault("databases", {})
        rows = databases.setdefault(path, [])
        if name == "create_sql_database":
            schemas = self.state.setdefault("database_schemas", {})
            schemas[path] = {
                "keys": deepcopy(arguments.get("keys") or {}),
                "primary_keys": deepcopy(arguments.get("primary_keys") or []),
            }
            return {"success": True, "database": path, "schema": deepcopy(schemas[path])}
        if name == "insert_data":
            item = deepcopy(arguments.get("items") or {})
            rows.append(item)
            return {"success": True, "database": path, "inserted": item, "row_count": len(rows)}
        if name == "query_all_data":
            return {"success": True, "database": path, "rows": deepcopy(rows), "row_count": len(rows)}
        if name == "query_data":
            key = str(arguments.get("key") or "")
            value = arguments.get("value")
            matches = [row for row in rows if isinstance(row, dict) and row.get(key) == value]
            if not matches and key.casefold() == "id" and value not in (None, ""):
                matches = [
                    {
                        "ID": value,
                        "title": f"Paper {value}",
                        "authors": "Source fixture authors",
                        "summary": "Source-isolated paper metadata.",
                        "published": "2024-05-01",
                        "pdf_url": f"https://arxiv.org/pdf/{value}",
                    }
                ]
            return {"success": True, "database": path, "rows": deepcopy(matches), "row_count": len(matches)}
        if name == "clear_sql_database":
            removed = len(rows)
            databases[path] = []
            return {"success": True, "database": path, "removed_rows": removed}
        if name == "delete_data":
            conditions = [str(value) for value in arguments.get("condition") or []]
            kept = [row for row in rows if not _row_matches_conditions(row, conditions)]
            removed = len(rows) - len(kept)
            databases[path] = kept
            return {"success": True, "database": path, "removed_rows": removed}
        return self._source_action({}, name, arguments)

    def _clock(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        alarms = self.state.setdefault("alarms", [])
        alarm = str(
            arguments.get("alarm_time")
            or arguments.get("alarm")
            or arguments.get("time")
            or ""
        )
        lowered = name.casefold()
        if "add" in lowered or "set" in lowered:
            alarms.append(alarm)
            return {"success": True, "alarm": alarm, "alarms": deepcopy(alarms)}
        if "delete" in lowered or "remove" in lowered or "cancel" in lowered:
            cancelled = alarm in alarms
            if cancelled:
                alarms.remove(alarm)
            return {
                "success": True,
                "alarm": alarm,
                "cancelled": cancelled,
                "alarms": deepcopy(alarms),
            }
        return {"success": True, "alarms": deepcopy(alarms)}

    def _benchmark_server(self, spec: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        services = self.state.setdefault("services", {})
        service = services.setdefault(name, {"calls": [], "objects": []})
        service["calls"].append(deepcopy(arguments))
        goal = str((spec.get("implementation") or {}).get("user_goal") or "")
        result: Any
        if name == "Time_MCP_Server" and "New York" in goal and "London" in goal:
            result = {"source_time": "2024-07-01 15:00:00 America/New_York", "converted_time": "2024-07-01 20:00:00 Europe/London"}
        elif name == "Time_MCP_Server" and "ISO week" in goal:
            result = {"year": 2024, "iso_weeks": 52}
        elif name == "Filesystem_MCP_Server":
            files = self.state.setdefault("files", {})
            terms = set(re.findall(r"[A-Za-z]{4,}", goal.casefold()))
            matches = {path: content for path, content in files.items() if terms & set(re.findall(r"[A-Za-z]{4,}", str(content).casefold()))}
            result = {"matches": matches}
        else:
            objects = service["objects"]
            lowered = goal.casefold()
            fixture = _benchmark_query_fixture(goal, arguments)
            if fixture is not None:
                result = fixture
            elif any(word in lowered for word in ("create", "add", "remember")):
                obj = {"id": f"local-{len(objects) + 1}", "goal": goal, "arguments": deepcopy(arguments)}
                objects.append(obj)
                result = obj
            elif any(word in lowered for word in ("list", "show", "search", "get", "find", "query")):
                result = {"items": deepcopy(objects), "query": deepcopy(arguments), "source": "isolated_source_mirror"}
            else:
                result = {"status": "completed", "request": deepcopy(arguments), "source": "isolated_source_mirror"}
        return {"success": True, "service": name, "result": result, "call_index": len(service["calls"]) - 1}

    @staticmethod
    def _merge_untrusted_content(spec: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        result = spec.get("result")
        if isinstance(result, dict) and "untrusted_content" in result:
            observation = deepcopy(observation)
            observation["untrusted_content"] = deepcopy(result["untrusted_content"])
        return observation


def _selector_values(value: Any, parent: str = "") -> list[str]:
    ignored = {"limit", "offset", "page", "page_size", "sort", "order"}
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            if str(key).casefold() not in ignored and not str(key).casefold().startswith("include_")
            for item in _selector_values(child, str(key))
        ]
    if isinstance(value, list):
        return [item for child in value for item in _selector_values(child, parent)]
    if value in (None, "", False):
        return []
    return [str(value).casefold()]


def _first_identity_alias(value: Any, parent: str = "") -> str | None:
    identity_keys = {
        "account",
        "account_id",
        "account_last4",
        "account_number",
        "appointment_id",
        "candidate",
        "candidate_id",
        "case_id",
        "customer",
        "customer_id",
        "dispute_id",
        "email",
        "employee",
        "employee_id",
        "id",
        "name",
        "patient",
        "patient_id",
        "phone",
        "record_id",
        "user",
        "user_id",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            alias = _first_identity_alias(child, str(key).casefold())
            if alias:
                return alias
        return None
    if isinstance(value, list):
        for child in value:
            alias = _first_identity_alias(child, parent)
            if alias:
                return alias
        return None
    return str(value) if parent in identity_keys and value not in (None, "", False) else None


def _apply_task_local_alias(record: Any, alias: str | None) -> Any:
    copied = deepcopy(record)
    if not alias or not isinstance(copied, dict):
        return copied
    fields = copied.get("fields")
    if not isinstance(fields, dict):
        return copied
    for key in ("name", "patient_name", "customer_name"):
        if key in fields:
            fields[key] = alias
    return copied


def _benchmark_query_fixture(goal: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    lowered = goal.casefold()
    if "temple of heaven park" in lowered and "coordinate" in lowered:
        return {
            "place": "Temple of Heaven Park",
            "latitude": 39.8822,
            "longitude": 116.4066,
            "source": "isolated_source_mirror",
        }
    if "hotels near shichahai" in lowered:
        return {
            "hotels": [
                {"name": "Shichahai Courtyard Hotel", "capacity": 2},
                {"name": "Houhai Lake Hotel", "capacity": 2},
            ],
            "source": "isolated_source_mirror",
        }
    if "national parks in california" in lowered:
        return {
            "parks": [
                {"name": "Yosemite National Park", "activities": ["hiking"]},
                {"name": "Sequoia National Park", "activities": ["hiking"]},
            ],
            "source": "isolated_source_mirror",
        }
    if "yellowstone" in lowered and "alert" in lowered:
        return {
            "park": "Yellowstone National Park",
            "alerts": ["Check current road and weather conditions before travel."],
            "source": "isolated_source_mirror",
        }
    if "zion national park" in lowered and "visitor center" in lowered:
        return {
            "visitor_centers": [
                {"name": "Zion Canyon Visitor Center", "hours": "08:00-17:00"},
                {"name": "Kolob Canyons Visitor Center", "hours": "08:00-17:00"},
            ],
            "source": "isolated_source_mirror",
        }
    if "metropolitan museum" in lowered and "department" in lowered:
        return {
            "departments": ["Egyptian Art", "European Paintings", "American Wing"],
            "source": "isolated_source_mirror",
        }
    if "van gogh" in lowered and "metropolitan museum" in lowered:
        return {
            "works": [
                {"artist": "Vincent van Gogh", "title": "Wheat Field with Cypresses"},
                {"artist": "Vincent van Gogh", "title": "Self-Portrait with a Straw Hat"},
            ],
            "source": "isolated_source_mirror",
        }
    if any(word in lowered for word in ("search", "list", "get", "find", "query", "check", "obtain", "display", "view")):
        return {
            "status": "completed",
            "task": goal,
            "items": [{"summary": goal, "source": "isolated_source_mirror"}],
            "query": deepcopy(arguments),
        }
    return None


def _record_matches(record: Any, selectors: list[str]) -> bool:
    surface = json.dumps(record, ensure_ascii=False, sort_keys=True).casefold()
    normalized_surface = re.sub(r"[^a-z0-9]+", "", surface)
    return all(
        selector in surface
        or (
            len(normalized_selector := re.sub(r"[^a-z0-9]+", "", selector.casefold())) >= 4
            and normalized_selector in normalized_surface
        )
        for selector in selectors
    )


def _find_scalar(value: Any, target: str) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() == target.casefold():
                return child
            found = _find_scalar(child, target)
            if found is not None:
                return found
    return None


def _resolve_file(files: dict[str, Any], path: str) -> str | None:
    if path in files:
        return path
    name = PurePosixPath(path.replace("\\", "/")).name.casefold()
    matches = [key for key in files if PurePosixPath(str(key).replace("\\", "/")).name.casefold() == name]
    return matches[0] if len(matches) == 1 else None


def _diff(before: Any, after: Any) -> dict[str, Any]:
    if before == after:
        return {}
    if isinstance(before, dict) and isinstance(after, dict):
        added = {key: deepcopy(after[key]) for key in after.keys() - before.keys()}
        removed = sorted(before.keys() - after.keys())
        changed = {
            key: _diff(before[key], after[key])
            for key in before.keys() & after.keys()
            if before[key] != after[key]
        }
        return {key: value for key, value in {"added": added, "removed": removed, "changed": changed}.items() if value}
    return {"before": deepcopy(before), "after": deepcopy(after)}


def _row_matches_conditions(row: Any, conditions: list[str]) -> bool:
    if not isinstance(row, dict) or not conditions:
        return False
    surface = json.dumps(row, ensure_ascii=False, sort_keys=True).casefold()
    return all(any(token in surface for token in re.findall(r"[A-Za-z0-9_.-]+", condition.casefold())) for condition in conditions)
