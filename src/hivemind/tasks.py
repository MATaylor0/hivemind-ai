"""Tasks: the unit of work between agents, and between people and agents.

Every delegation, escalation and review is a task in a queue. A task has one assignee, a status, a parent (the
task it was created to serve), a root (the request that started the chain) and an optional goal, so every
piece of work can be traced back to why it exists.

The default store is a SharePoint list ("Agent Tasks") on the HiveMind site. Agents create and work tasks
through the hub, and people can also add a task for an agent, watch progress and read results in SharePoint,
Teams or the Lists app. ``InMemoryTaskStore`` is for development and tests.

Concurrency: claiming a task and delivering its result are compare-and-set operations (an ETag ``If-Match``
in SharePoint), so with several hub replicas a task is still worked once and its result delivered once.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from .graph import GraphClient, PreconditionFailed

if TYPE_CHECKING:
    from .loader import Org

Status = Literal["Queued", "InProgress", "Done", "Failed", "Cancelled"]
Kind = Literal["task", "escalation", "review", "follow_up"]
OPEN: tuple[str, ...] = ("Queued", "InProgress")


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Task:
    title: str
    instructions: str
    assigned_to: str
    requested_by: str
    requester_kind: Literal["agent", "person"] = "agent"
    kind: Kind = "task"
    status: Status = "Queued"
    parent_id: str | None = None
    root_id: str | None = None
    goal: str | None = None
    depth: int = 1
    result: str = ""
    output_url: str = ""
    error: str = ""
    attempts: int = 0
    delivered: bool = False
    lease_until: datetime | None = None
    created_at: datetime = field(default_factory=now)
    updated_at: datetime = field(default_factory=now)
    id: str = ""
    etag: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("etag")
        for key in ("lease_until", "created_at", "updated_at"):
            data[key] = data[key].isoformat() if data[key] else None
        return data

    def summary(self) -> dict[str, str]:
        out = {"task_id": self.id, "title": self.title, "assigned_to": self.assigned_to, "status": self.status}
        if self.result:
            out["result"] = self.result
        if self.output_url:
            out["output"] = self.output_url
        if self.error:
            out["error"] = self.error
        return out


class TaskStore(Protocol):
    def create(self, task: Task) -> Task: ...

    def get(self, task_id: str) -> Task | None: ...

    def query(
        self,
        status: str | None = None,
        assigned_to: str | None = None,
        requested_by: str | None = None,
        root_id: str | None = None,
        limit: int = 100,
    ) -> list[Task]: ...

    def transition(self, task: Task, **changes: Any) -> Task | None:
        """Apply ``changes`` only if the task is unchanged since it was read. None if someone else got there first."""
        ...

    def update(self, task: Task, **changes: Any) -> Task:
        """Apply ``changes`` unconditionally (used by the worker that holds the task's lease)."""
        ...


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Condition()
        self._seq = 0

    def create(self, task: Task) -> Task:
        with self._lock:
            self._seq += 1
            stored = replace(task, id=f"T-{self._seq}", etag="1")
            if stored.root_id is None:
                stored.root_id = stored.id
            self._tasks[stored.id] = stored
            self._lock.notify_all()
            return replace(stored)

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return replace(task) if task else None

    def query(self, status=None, assigned_to=None, requested_by=None, root_id=None, limit=100) -> list[Task]:
        with self._lock:
            out = [
                replace(t)
                for t in self._tasks.values()
                if (status is None or t.status == status)
                and (assigned_to is None or t.assigned_to == assigned_to)
                and (requested_by is None or t.requested_by == requested_by)
                and (root_id is None or t.root_id == root_id)
            ]
        return sorted(out, key=lambda t: t.created_at)[:limit]

    def transition(self, task: Task, **changes: Any) -> Task | None:
        with self._lock:
            current = self._tasks.get(task.id)
            if current is None or current.etag != task.etag:
                return None
            return self._apply(current, changes)

    def update(self, task: Task, **changes: Any) -> Task:
        with self._lock:
            return self._apply(self._tasks[task.id], changes)

    def _apply(self, current: Task, changes: dict[str, Any]) -> Task:
        updated = replace(current, **changes, updated_at=now(), etag=str(int(current.etag) + 1))
        self._tasks[current.id] = updated
        self._lock.notify_all()
        return replace(updated)


# --------------------------------------------------------------------------------------------------
# SharePoint list store
# --------------------------------------------------------------------------------------------------

# Column internal name -> (Task attribute, Graph column definition). Used by the store and by provisioning.
COLUMNS: dict[str, tuple[str, dict[str, Any]]] = {
    "AssignedAgent": ("assigned_to", {"text": {}, "indexed": True}),
    "Status": (
        "status",
        {
            "choice": {"choices": ["Queued", "InProgress", "Done", "Failed", "Cancelled"]},
            "defaultValue": {"value": "Queued"},
            "indexed": True,
        },
    ),
    "Instructions": ("instructions", {"text": {"allowMultipleLines": True, "linesForEditing": 8}}),
    "Result": ("result", {"text": {"allowMultipleLines": True, "linesForEditing": 8}}),
    "OutputLink": ("output_url", {"text": {}}),
    "RequestedBy": ("requested_by", {"text": {}}),
    "RequesterKind": (
        "requester_kind",
        {"choice": {"choices": ["agent", "person"]}, "defaultValue": {"value": "person"}},
    ),
    "TaskKind": (
        "kind",
        {"choice": {"choices": ["task", "escalation", "review", "follow_up"]}, "defaultValue": {"value": "task"}},
    ),
    "ParentTask": ("parent_id", {"text": {}}),
    "RootTask": ("root_id", {"text": {}, "indexed": True}),
    "Goal": ("goal", {"text": {}}),
    "Depth": ("depth", {"number": {}, "defaultValue": {"value": "1"}}),
    "Attempts": ("attempts", {"number": {}, "defaultValue": {"value": "0"}}),
    "Delivered": ("delivered", {"boolean": {}, "defaultValue": {"value": "0"}}),
    "LeaseUntil": ("lease_until", {"dateTime": {}}),
    "ErrorMessage": ("error", {"text": {"allowMultipleLines": True}}),
}
_ATTR_TO_COLUMN = {attr: column for column, (attr, _) in COLUMNS.items()}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class SharePointTaskStore:
    """Tasks as items in a SharePoint list. Task ids are ``T-<list item id>``."""

    PREFER = {"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"}

    def __init__(self, graph: GraphClient, site: str, list_name: str):
        self.graph = graph
        self.site = site
        self.list_name = list_name

    def _base(self) -> str:
        return f"/sites/{self.graph.site_id(self.site)}/lists/{self.graph.list_id(self.site, self.list_name)}"

    @staticmethod
    def _fields(task: Task, changes: dict[str, Any] | None = None) -> dict[str, Any]:
        source = changes if changes is not None else {a: getattr(task, a) for a in _ATTR_TO_COLUMN}
        out: dict[str, Any] = {}
        for attr, value in source.items():
            if attr == "title":
                out["Title"] = value
                continue
            column = _ATTR_TO_COLUMN.get(attr)
            if column is None:
                continue
            if isinstance(value, datetime):
                value = value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            out[column] = value if value is not None else ""
        if changes is None:
            out["Title"] = task.title
        return out

    @staticmethod
    def _from_item(item: dict[str, Any]) -> Task:
        f = item.get("fields", {})
        created_by = (item.get("createdBy") or {}).get("user") or {}
        requested_by = f.get("RequestedBy") or created_by.get("email") or created_by.get("displayName") or "unknown"
        task_id = f"T-{item['id']}"
        return Task(
            id=task_id,
            etag=item.get("eTag") or f.get("@odata.etag", ""),
            title=f.get("Title") or "(untitled)",
            instructions=f.get("Instructions") or f.get("Title") or "",
            assigned_to=(f.get("AssignedAgent") or "").strip(),
            requested_by=requested_by,
            requester_kind=f.get("RequesterKind") or ("agent" if f.get("RequestedBy") else "person"),
            kind=f.get("TaskKind") or "task",
            status=f.get("Status") or "Queued",
            parent_id=f.get("ParentTask") or None,
            root_id=f.get("RootTask") or task_id,
            goal=f.get("Goal") or None,
            depth=int(f.get("Depth") or (1 if f.get("RequestedBy") else 0)),
            result=f.get("Result") or "",
            output_url=f.get("OutputLink") or "",
            error=f.get("ErrorMessage") or "",
            attempts=int(f.get("Attempts") or 0),
            delivered=bool(f.get("Delivered")),
            lease_until=_parse_dt(f.get("LeaseUntil")),
            created_at=_parse_dt(item.get("createdDateTime")) or now(),
            updated_at=_parse_dt(item.get("lastModifiedDateTime")) or now(),
        )

    def create(self, task: Task) -> Task:
        item = self.graph.request("POST", f"{self._base()}/items", {"fields": self._fields(task)})
        created = self._from_item(item)
        if not task.root_id:
            self.graph.request("PATCH", f"{self._base()}/items/{item['id']}/fields", {"RootTask": created.id})
            created.root_id = created.id
        return created

    def get(self, task_id: str) -> Task | None:
        item_id = task_id.removeprefix("T-")
        if not item_id.isdigit():
            return None
        try:
            return self._from_item(self.graph.request("GET", f"{self._base()}/items/{item_id}?expand=fields"))
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status", None) == 404:
                return None
            raise

    def query(self, status=None, assigned_to=None, requested_by=None, root_id=None, limit=100) -> list[Task]:
        filters = []
        if status:
            filters.append(f"fields/Status eq '{status}'")
        if assigned_to:
            filters.append(f"fields/AssignedAgent eq '{assigned_to}'")
        if root_id:
            filters.append(f"fields/RootTask eq '{root_id}'")
        query = f"?expand=fields&$top={min(limit, 500)}"
        if filters:
            query += "&$filter=" + " and ".join(filters)
        data = self.graph.request("GET", f"{self._base()}/items{query}", headers=self.PREFER)
        tasks = [self._from_item(i) for i in data.get("value", [])]
        # Filter again client-side: cheap, and correct even if the server ignored a non-indexed filter.
        tasks = [
            t
            for t in tasks
            if (status is None or t.status == status)
            and (assigned_to is None or t.assigned_to == assigned_to)
            and (requested_by is None or t.requested_by == requested_by)
            and (root_id is None or t.root_id == root_id)
        ]
        return sorted(tasks, key=lambda t: t.created_at)[:limit]

    def transition(self, task: Task, **changes: Any) -> Task | None:
        headers = {"If-Match": task.etag} if task.etag else {}
        try:
            self.graph.request(
                "PATCH", f"{self._base()}/items/{task.id[2:]}/fields", self._fields(task, changes), headers=headers
            )
        except PreconditionFailed:
            return None
        return self.get(task.id)

    def update(self, task: Task, **changes: Any) -> Task:
        self.graph.request("PATCH", f"{self._base()}/items/{task.id[2:]}/fields", self._fields(task, changes))
        return self.get(task.id) or replace(task, **changes)


def lease(minutes: int) -> datetime:
    return now() + timedelta(minutes=minutes)


class JsonFileTaskStore(InMemoryTaskStore):
    """In-memory store persisted to a JSON file, so the local CLI and a local hub share one queue."""

    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        self._file_lock = threading.Lock()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._seq = data["seq"]
        self._tasks = {}
        for raw in data["tasks"]:
            for key in ("lease_until", "created_at", "updated_at"):
                raw[key] = _parse_dt(raw[key])
            self._tasks[raw["id"]] = Task(**raw)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tasks = []
        for task in self._tasks.values():
            data = task.to_dict()
            data["etag"] = task.etag
            tasks.append(data)
        self.path.write_text(json.dumps({"seq": self._seq, "tasks": tasks}, indent=2), encoding="utf-8")

    def _locked(self, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._file_lock:
            self._load()
            result = getattr(super(), method)(*args, **kwargs)
            if method in ("create", "transition", "update"):
                self._save()
            return result

    def create(self, task: Task) -> Task:
        return self._locked("create", task)

    def get(self, task_id: str) -> Task | None:
        return self._locked("get", task_id)

    def query(self, status=None, assigned_to=None, requested_by=None, root_id=None, limit=100) -> list[Task]:
        return self._locked("query", status, assigned_to, requested_by, root_id, limit)

    def transition(self, task: Task, **changes: Any) -> Task | None:
        return self._locked("transition", task, **changes)

    def update(self, task: Task, **changes: Any) -> Task:
        return self._locked("update", task, **changes)


def task_store_for(org: Org, graph: GraphClient | None = None) -> TaskStore:
    """Local environments share a JSON file; Foundry environments use the SharePoint list (or memory)."""
    env = org.env
    if env.provider == "local" or env.sharepoint_local_dir:
        return JsonFileTaskStore(org.root / ".hivemind" / (org.env_name or "local") / "tasks.json")
    if org.config.hub.tasks.backend == "sharepoint":
        if org.config.sharepoint is None:
            raise ValueError("hub.tasks.backend is 'sharepoint' but no sharepoint site is configured")
        site = env.sharepoint_site or org.config.sharepoint.site
        return SharePointTaskStore(graph or GraphClient(), site, org.config.sharepoint.tasks_list)
    return InMemoryTaskStore()
