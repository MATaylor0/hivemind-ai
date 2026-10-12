"""A deliberately small Microsoft Graph client for SharePoint (sites, drives, lists).

Only the handful of calls HiveMind needs. Authentication is any callable returning a bearer token; by default
``DefaultAzureCredential`` (managed identity in Azure, ``az login`` locally). Grant the identity
``Sites.Selected`` on the HiveMind site rather than tenant-wide SharePoint permissions.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

GRAPH = "https://graph.microsoft.com/v1.0"
TokenProvider = Callable[[], str]


class GraphError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Graph {status}: {message}")
        self.status = status


class PreconditionFailed(GraphError):
    """412: the item changed since it was read (used for optimistic concurrency)."""


def default_token_provider() -> TokenProvider:
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError("SharePoint access needs the 'azure' extra: pip install 'hivemind-ai[azure]'") from exc
    credential = DefaultAzureCredential()
    return lambda: credential.get_token("https://graph.microsoft.com/.default").token


@dataclass
class DriveItem:
    id: str
    name: str
    is_folder: bool
    web_url: str = ""
    etag: str = ""


class GraphClient:
    def __init__(self, token: TokenProvider | None = None):
        self._token = token
        self._site_ids: dict[str, str] = {}
        self._drive_ids: dict[tuple[str, str], str] = {}
        self._list_ids: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------ transport

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        url = path if path.startswith("https://") else f"{GRAPH}{path}"
        data: bytes | None = None
        all_headers = {"Authorization": f"Bearer {self.token()}"}
        if isinstance(body, bytes):
            data = body
            all_headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        req = urllib.request.Request(url, data=data, method=method, headers=all_headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:  # noqa: S310 - fixed https host
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code == 412:
                raise PreconditionFailed(exc.code, detail) from exc
            raise GraphError(exc.code, detail) from exc
        except OSError as exc:
            raise GraphError(0, str(exc)) from exc
        if raw:
            return payload
        return json.loads(payload) if payload else {}

    def token(self) -> str:
        if self._token is None:
            self._token = default_token_provider()
        return self._token()

    # ------------------------------------------------------------------ sites, drives, lists

    def site_id(self, site: str) -> str:
        """``contoso.sharepoint.com/sites/HiveMind`` -> Graph site id."""
        if site not in self._site_ids:
            host, _, path = site.removeprefix("https://").partition("/")
            self._site_ids[site] = self.request("GET", f"/sites/{host}:/{path}")["id"]
        return self._site_ids[site]

    def drive_id(self, site: str, library: str | None = None) -> str:
        key = (site, library or "")
        if key not in self._drive_ids:
            sid = self.site_id(site)
            if not library:
                self._drive_ids[key] = self.request("GET", f"/sites/{sid}/drive")["id"]
            else:
                drives = self.request("GET", f"/sites/{sid}/drives")["value"]
                match = [d for d in drives if d.get("name") == library]
                if not match:
                    raise GraphError(404, f"document library '{library}' not found on {site}")
                self._drive_ids[key] = match[0]["id"]
        return self._drive_ids[key]

    def list_id(self, site: str, list_name: str) -> str:
        key = (site, list_name)
        if key not in self._list_ids:
            sid = self.site_id(site)
            self._list_ids[key] = self.request("GET", f"/sites/{sid}/lists/{_quote(list_name)}")["id"]
        return self._list_ids[key]

    # ------------------------------------------------------------------ files

    def children(self, drive_id: str, folder: str) -> list[DriveItem]:
        try:
            data = self.request("GET", f"/drives/{drive_id}/root:/{_quote(folder)}:/children")
        except GraphError as exc:
            if exc.status == 404:
                return []
            raise
        return [
            DriveItem(i["id"], i["name"], "folder" in i, i.get("webUrl", ""), i.get("eTag", ""))
            for i in data.get("value", [])
        ]

    def download(self, drive_id: str, item_id: str) -> bytes:
        return self.request("GET", f"/drives/{drive_id}/items/{item_id}/content", raw=True)

    def download_path(self, drive_id: str, path: str) -> bytes:
        return self.request("GET", f"/drives/{drive_id}/root:/{_quote(path)}:/content", raw=True)

    def upload(self, drive_id: str, path: str, content: bytes) -> DriveItem:
        """Simple upload (files up to 4 MB), replacing any existing file."""
        item = self.request("PUT", f"/drives/{drive_id}/root:/{_quote(path)}:/content", body=content)
        return DriveItem(item["id"], item["name"], False, item.get("webUrl", ""), item.get("eTag", ""))

    def ensure_folder(self, drive_id: str, path: str) -> None:
        parent = ""
        for part in [p for p in path.split("/") if p]:
            root = f"/drives/{drive_id}/root"
            target = f"{root}:/{_quote(parent)}:/children" if parent else f"{root}/children"
            try:
                self.request("POST", target, {"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
            except GraphError as exc:
                if exc.status != 409:  # already exists
                    raise
            parent = f"{parent}/{part}" if parent else part


def _quote(path: str) -> str:
    return urllib.parse.quote(path.strip("/"))
