"""Google Drive as an Aggrete upstream, one tool pair per folder.

    python -m aggrete.connectors.drive --credentials sa.json --root "Northwind"

The proxy runs this over stdio and holds the service-account key; people never
do. Each subfolder of the root becomes two tools, `search_<folder>` and
`read_<folder>`, so the policy can name folders as domains:

    domains:
      "drive__*_restructuring_plan": restructuring-plan
      "drive__*_legal_hold": legal-hold
      "drive__*": drive-general

Results carry the owner's and last editor's email, which is what the policy
counts as "people". Read only: the service account needs Viewer on the root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import httpx2 as httpx
import jwt
from mcp.server.mcpserver import MCPServer

API = "https://www.googleapis.com/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
EXPORT = {"application/vnd.google-apps.document": "text/plain",
          "application/vnd.google-apps.spreadsheet": "text/csv",
          "application/vnd.google-apps.presentation": "text/plain"}
FIELDS = "files(id,name,mimeType,modifiedTime,owners(emailAddress,displayName),lastModifyingUser(emailAddress),parents,webViewLink)"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class Drive:
    """Minimal Drive v3 client on a service account. No Google SDK: PyJWT + httpx."""

    def __init__(self, credentials: str | Path):
        self.sa = json.loads(Path(credentials).read_text())
        self._tok, self._exp = None, 0.0
        self.http = httpx.Client(timeout=30)

    def token(self) -> str:
        if self._tok and time.time() < self._exp - 60:
            return self._tok
        now = int(time.time())
        assertion = jwt.encode({"iss": self.sa["client_email"], "scope": SCOPE, "aud": self.sa["token_uri"],
                                "iat": now, "exp": now + 3600}, self.sa["private_key"], algorithm="RS256")
        r = self.http.post(self.sa["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
        r.raise_for_status()
        self._tok, self._exp = r.json()["access_token"], time.time() + r.json().get("expires_in", 3600)
        return self._tok

    def get(self, path: str, **params):
        r = self.http.get(API + path, params=params, headers={"Authorization": f"Bearer {self.token()}"})
        r.raise_for_status()
        return r

    def list(self, q: str, page_size: int = 20) -> list[dict]:
        return self.get("/files", q=q, fields=FIELDS, pageSize=page_size, supportsAllDrives="true",
                        includeItemsFromAllDrives="true").json().get("files", [])

    def folder_by_name(self, name: str) -> dict | None:
        fs = self.list(f"mimeType='application/vnd.google-apps.folder' and name='{name}' and trashed=false", 5)
        return fs[0] if fs else None

    def subfolders(self, folder_id: str) -> list[dict]:
        return self.list(f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false", 50)

    def descendants(self, folder_id: str, depth: int = 3) -> set[str]:
        ids, frontier = {folder_id}, [folder_id]
        for _ in range(depth):
            nxt = []
            for f in frontier:
                for sub in self.subfolders(f):
                    if sub["id"] not in ids:
                        ids.add(sub["id"]); nxt.append(sub["id"])
            frontier = nxt
        return ids

    def search(self, folder_id: str, query: str) -> list[dict]:
        out = []
        for fid in self.descendants(folder_id):
            q = f"'{fid}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
            if query.strip():
                safe = query.replace("'", "\\'")
                q += f" and (fullText contains '{safe}' or name contains '{safe}')"
            out += self.list(q, 20)
        return out

    def read(self, file_id: str, folder_id: str) -> tuple[dict, str]:
        meta = self.get(f"/files/{file_id}", fields="id,name,mimeType,parents,owners(emailAddress,displayName),lastModifyingUser(emailAddress),webViewLink", supportsAllDrives="true").json()
        if not (set(meta.get("parents") or []) & self.descendants(folder_id)):
            raise PermissionError("that file is not in this folder")
        mime = meta.get("mimeType", "")
        if mime in EXPORT:
            text = self.get(f"/files/{file_id}/export", mimeType=EXPORT[mime]).text
        elif mime.startswith("text/") or mime in ("application/json",):
            text = self.get(f"/files/{file_id}", alt="media", supportsAllDrives="true").text
        else:
            text = f"[{mime}: binary file, {meta.get('name')}; not rendered]"
        return meta, text[:20000]


def build(drive: Drive, root_name: str) -> MCPServer:
    server = MCPServer("drive")
    root = drive.folder_by_name(root_name)
    if not root:
        # Stay up so the proxy starts; tell whoever asks what is missing.
        @server.tool(name="status", description="Why no Drive folders are available yet.")
        def status() -> str:
            return json.dumps({"error": f"No folder named {root_name!r} is shared with {drive.sa['client_email']}. "
                                        "Share it (Viewer) and restart the proxy."})
        return server
    folders = drive.subfolders(root["id"]) or [root]

    @server.tool(name="folders", description="List the Google Drive folders you can search here. Call this first when asked about documents, files or anything in Google Drive.")
    def folders_tool() -> str:
        return json.dumps({"drive_folders": [{"name": f["name"], "search_tool": f"search_{slug(f['name'])}", "read_tool": f"read_{slug(f['name'])}"} for f in folders]})

    for f in folders:
        s = slug(f["name"]); fid = f["id"]; label = f["name"]

        def make(fid=fid, label=label):
            def search(query: str = "") -> str:
                return json.dumps({"folder": label, "files": [
                    {"id": x["id"], "name": x["name"], "type": x.get("mimeType"), "modified": x.get("modifiedTime"),
                     "owner_email": (x.get("owners") or [{}])[0].get("emailAddress"),
                     "editor_email": (x.get("lastModifyingUser") or {}).get("emailAddress"), "link": x.get("webViewLink")}
                    for x in drive.search(fid, query)]})
            def read(file_id: str) -> str:
                meta, text = drive.read(file_id, fid)
                return json.dumps({"folder": label, "name": meta["name"], "owner_email": (meta.get("owners") or [{}])[0].get("emailAddress"),
                                   "editor_email": (meta.get("lastModifyingUser") or {}).get("emailAddress"), "text": text})
            return search, read

        search, read = make()
        sdesc = f"Search Google Drive for documents in the '{label}' folder, by words in the title or full text. Use this to look for {label.lower()} in Drive; leave the query empty to list everything in the folder."
        rdesc = f"Read a Google Drive document from the '{label}' folder (Docs, Sheets and Slides come back as text)."
        server.tool(name=f"search_{s}", description=sdesc)(search)
        server.tool(name=f"read_{s}", description=rdesc)(read)
    return server


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--credentials", required=True, help="service-account JSON key")
    ap.add_argument("--root", default="Aggrete", help="name of the shared root folder; its subfolders become tools")
    ap.add_argument("--list", action="store_true", help="print the tools that would be exposed and exit")
    a = ap.parse_args()
    drive = Drive(a.credentials)
    if a.list:
        root = drive.folder_by_name(a.root)
        print("root:", root["name"] if root else None, root["id"] if root else "")
        for f in (drive.subfolders(root["id"]) if root else []):
            print(f"  {f['name']!r:32} -> drive__search_{slug(f['name'])}, drive__read_{slug(f['name'])}")
        return
    asyncio.run(build(drive, a.root).run_stdio_async())


if __name__ == "__main__":
    main()
