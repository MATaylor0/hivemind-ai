"""Shared memory backends for the hub. ACLs are enforced by ``HubService``, not here."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass
class MemoryRecord:
    scope: str
    content: str
    author: str
    created_at: str
    id: str = ""


class MemoryBackend(Protocol):
    def write(self, scope: str, content: str, author: str) -> MemoryRecord: ...

    def search(self, scope: str, query: str, limit: int = 5) -> list[MemoryRecord]: ...


_WORD = re.compile(r"[a-z0-9]+")


class InMemoryBackend:
    """Process-local keyword search. For development and tests only."""

    def __init__(self) -> None:
        self._records: list[MemoryRecord] = []
        self._lock = threading.Lock()

    def write(self, scope: str, content: str, author: str) -> MemoryRecord:
        with self._lock:
            record = MemoryRecord(scope, content, author, datetime.now(UTC).isoformat(), id=str(len(self._records) + 1))
            self._records.append(record)
            return record

    def search(self, scope: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        terms = set(_WORD.findall(query.lower()))
        with self._lock:
            candidates = [r for r in self._records if r.scope == scope]
        if not terms:
            return candidates[-limit:][::-1]
        scored = [(len(terms & set(_WORD.findall(r.content.lower()))), i, r) for i, r in enumerate(candidates)]
        scored = [s for s in scored if s[0] > 0]
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [r for _, _, r in scored[:limit]]


class FoundryMemoryBackend:
    """Foundry memory store (semantic extraction + search). One store, one Foundry scope per hub scope."""

    def __init__(self, client: Any, store: str, kind: str = "procedural"):
        self.client = client
        self.store = store
        self.kind = kind

    def write(self, scope: str, content: str, author: str) -> MemoryRecord:
        item = self.client.beta.memory_stores.create_memory(
            self.store, scope=scope, content=f"[{author}] {content}", kind=self.kind
        )
        return MemoryRecord(scope, content, author, datetime.now(UTC).isoformat(), id=str(item.memory_id))

    def search(self, scope: str, query: str, limit: int = 5) -> list[MemoryRecord]:
        from azure.ai.projects.models import MemorySearchOptions

        result = self.client.beta.memory_stores.search_memories(
            self.store, scope=scope, items=query, options=MemorySearchOptions(max_memories=limit)
        )
        out = []
        for hit in result.memories:
            item = hit.memory_item
            content = item.content
            author = ""
            if content.startswith("[") and "] " in content:
                author, content = content[1:].split("] ", 1)
            out.append(MemoryRecord(scope, content, author, item.updated_at.isoformat(), id=str(item.memory_id)))
        return out
