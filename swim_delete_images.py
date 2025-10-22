#!/usr/bin/env python3
"""
Delete SWIM images from Cisco Catalyst Center by filters.

Features
- Auth via username/password (token auto-handled) or pre-supplied X-Auth-Token
- List images and filter by: family, name substring, version, regex, type, age (days), golden status, "unused only"
- Dry-run and --yes (no prompt) safety
- Optional "remove golden tag first" flow (needs siteId, deviceFamilyIdentifier, deviceRole)
- Polls async Task API where applicable
- Works with recent Catalyst Center releases (2.3.x “DNA Center” → “Catalyst Center”)
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

# --- lightweight dependency handling (requests) ---
try:
    import requests  # type: ignore
except Exception:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "requests"])
    import requests  # type: ignore

# ---------- Helpers ----------

def log(msg: str):
    print(msg, flush=True)

def die(msg: str, code: int = 1):
    log(f"ERROR: {msg}")
    sys.exit(code)

def parse_bool(s: Optional[str]) -> Optional[bool]:
    if s is None:
        return None
    s = s.strip().lower()
    if s in ("true", "t", "yes", "y", "1"):
        return True
    if s in ("false", "f", "no", "n", "0"):
        return False
    return None

# ---------- API wrapper ----------

class CatalystCenter:
    def __init__(self, base_url: str, username: Optional[str], password: Optional[str],
                 token: Optional[str], verify: bool):
        self.base = base_url.rstrip("/")
        self.verify = verify
        self.session = requests.Session()
        self.session.verify = verify
        self.session.headers.update({"Content-Type": "application/json"})
        if token:
            self.session.headers["X-Auth-Token"] = token
        elif username and password:
            self._login(username, password)
        else:
            die("Provide either --token OR --username/--password")

    def _login(self, username: str, password: str):
        # POST /dna/system/api/v1/auth/token
        url = f"{self.base}/dna/system/api/v1/auth/token"
        r = self.session.post(url, auth=(username, password))
        if r.status_code not in (200, 201):
            die(f"Auth failed ({r.status_code}): {r.text}")
        token = r.json().get("Token") or r.json().get("token")
        if not token:
            die("Auth succeeded but no token in response")
        self.session.headers["X-Auth-Token"] = token

    # Task poller: GET /dna/intent/api/v1/task/{taskId}
    def get_task(self, task_id: str, timeout: int = 300, poll_interval: float = 2.5) -> Dict[str, Any]:
        url = f"{self.base}/dna/intent/api/v1/task/{task_id}"
        start = time.time()
        while True:
            r = self.session.get(url)
            if r.status_code != 200:
                return {"error": f"task query failed {r.status_code}", "raw": r.text}
            data = r.json().get("response") or r.json()
            progress = (data or {}).get("progress")
            is_error = (data or {}).get("isError")
            failure = (data or {}).get("failureReason")
            if is_error or failure:
                return {"error": failure or "task reported error", "raw": data}
            if progress and any(k in str(progress).lower() for k in ("completed", "success", "done", "deletion")):
                return {"ok": True, "data": data}
            if time.time() - start > timeout:
                return {"error": "task timeout", "raw": data}
            time.sleep(poll_interval)

    # List images (SWIM “image importation” inventory)
    # GET /dna/intent/api/v1/image/importation?family=...&name=...&version=...
    def list_images(self, **params) -> List[Dict[str, Any]]:
        url = f"{self.base}/dna/intent/api/v1/image/importation"
        # Cisco’s SWIM guide uses this endpoint for query by family/name/version. (Ref) developer.cisco.com SWIM guide.
        r = self.session.get(url, params=params)
        if r.status_code != 200:
            die(f"List images failed ({r.status_code}): {r.text}")
        return r.json().get("response") or r.json()

    # Remove Golden tag (optional pre-step)
    # DELETE /dna/intent/api/v1/image/importation/golden/site/{siteId}/family/{deviceFamilyIdentifier}/role/{deviceRole}/image/{imageId}
    def remove_golden(self, site_id: str, family_id: str, role: str, image_id: str) -> Optional[str]:
        url = f"{self.base}/dna/intent/api/v1/image/importation/golden/site/{site_id}/family/{family_id}/role/{role}/image/{image_id}"
        r = self.session.delete(url)
        if r.status_code in (200, 202):
            body = r.json()
            task_id = (body.get("response") or {}).get("taskId")
            return task_id
        if r.status_code == 204:
            return None
        # Not fatal—maybe image wasn’t golden in that scope
        log(f"Warning: remove_golden failed {r.status_code}: {r.text}")
        return None

    # Delete image: API pathname changed over time and isn’t prominently documented;
    # we try the most likely DELETE endpoints with fallbacks and surface errors clearly.
    def delete_image(self, image_id: str) -> Dict[str, Any]:
        tried = []

        # 1) Likely path (newer): /dna/intent/api/v1/image/importation/{imageId}
        for path in [
            f"/dna/intent/api/v1/image/importation/{image_id}",
            f"/dna/intent/api/v1/image/{image_id}",  # fallback seen in some older builds
        ]:
            url = f"{self.base}{path}"
            r = self.session.delete(url)
            tried.append((path, r.status_code))
            if r.status_code in (200, 202):
                body = r.json()
                task_id = (body.get("response") or {}).get("taskId")
                return {"ok": True, "taskId": task_id, "path": path}
            if r.status_code == 204:
                return {"ok": True, "taskId": None, "path": path}
            # 409 could be “is golden” or “in use”; bubble up after trying both paths
        return {"ok": False, "tried": tried, "last_text": r.text}

# ---------- Filtering ----------

def compile_filters(args):
    v_regex = re.compile(args.version_regex) if args.version_regex else None
    name_regex = re.compile(args.name_regex) if args.name_regex else None
    older_than = dt.timedelta(days=args.older_than_days) if args.older_than_days else None
    now = dt.datetime.utcnow()

    def _match(img: Dict[str, Any]) -> bool:
        # Common fields found in /image/importation results (names vary slightly across versions)
        family = (img.get("family") or img.get("familyName") or "").lower()
        name = (img.get("name") or img.get("imageName") or "").lower()
        version = (img.get("version") or img.get("softwareVersion") or "").lower()
        image_type = (img.get("imageType") or img.get("type") or "").lower()
        is_golden = bool(img.get("isTaggedGolden") or img.get("isGolden") or img.get("golden"))
        # When imported?
        created = img.get("createdTime") or img.get("importedDate") or img.get("lastUpdateTime")
        created_dt = None
        if created:
            try:
                # Try ISO first, fallback epoch ms
                if isinstance(created, (int, float)):
                    created_dt = dt.datetime.utcfromtimestamp(int(created) / 1000 if int(created) > 10**10 else int(created))
                else:
                    created_dt = dt.datetime.fromisoformat(str(created).replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                created_dt = None

        if args.family and args.family.lower() not in family:
            return False
        if args.name_contains and args.name_contains.lower() not in name:
            return False
        if name_regex and not name_regex.search(name):
            return False
        if args.version and args.version.lower() != version:
            return False
        if v_regex and not v_regex.search(version):
            return False
        if args.type and args.type.lower() not in image_type:
            return False
        if args.golden is not None and is_golden != args.golden:
            return False
        if older_than and created_dt and (now - created_dt) < older_than:
            return False
        if args.unused_only:
            # Some payloads expose "usedDevicesCount" or "applicableDevicesCount".
            # We try to infer “unused” conservatively.
            used = img.get("usedDevicesCount") or img.get("usingDeviceCount") or img.get("deviceCount") or 0
            try:
                if int(used) > 0:
                    return False
            except Exception:
                pass
        return True

    return _match

# ---------- CLI ----------

def build_arg_parser():
    p = argparse.ArgumentParser(description="Delete Catalyst Center SWIM images by filters.")
    auth = p.add_argument_group("auth")
    auth.add_argument("--base-url", required=True, help="https://<catalyst-center-host>")
    auth.add_argument("--username", help="GUI/API username")
    auth.add_argument("--password", help="GUI/API password")
    auth.add_argument("--token", help="Use existing X-Auth-Token instead of username/password")
    auth.add_argument("--insecure", action="store_true", help="Skip TLS verification")

    flt = p.add_argument_group("filters")
    flt.add_argument("--family", help="Device family filter (e.g., cat9k)")
    flt.add_argument("--type", help="Image type filter (e.g., bin, smu, rommon)")
    flt.add_argument("--name-contains", help="Substring match on image name")
    flt.add_argument("--name-regex", help="Regex on image name")
    flt.add_argument("--version", help="Exact version match (e.g., 17.9.4a)")
    flt.add_argument("--version-regex", help="Regex for version (e.g., '^17\\.9\\.')")
    flt.add_argument("--golden", type=str, choices=["true","false","any"], default="any",
                     help="Filter by golden status")
    flt.add_argument("--older-than-days", type=int, help="Only images older than N days")
    flt.add_argument("--unused-only", action="store_true", help="Delete only images not used by any device")
    flt.add_argument("--limit", type=int, default=0, help="Stop after deleting N images (0 = no limit)")

    gold = p.add_argument_group("golden-tag (optional pre-step)")
    gold.add_argument("--site-id", help="Site UUID; use -1 for Global")
    gold.add_argument("--device-family-identifier", help="Device Family Identifier (numeric string)")
    gold.add_argument("--device-role", help="Device role (ALL|ACCESS|DISTRIBUTION|CORE|BORDER ROUTER|UNKNOWN)")

    safety = p.add_argument_group("safety & UX")
    safety.add_argument("--dry-run", action="store_true", help="Show what would be deleted; do nothing")
    safety.add_argument("--yes", action="store_true", help="Do not prompt; proceed (dangerous)")
    safety.add_argument("--json", action="store_true", help="Print result JSON for automation")

    return p

def confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except EOFError:
        return False

def main():
    ap = build_arg_parser()
    args = ap.parse_args()

    verify = not args.insecure
    golden_filter = {"true": True, "false": False, "any": None}[args.golden]

    cc = CatalystCenter(
        base_url=args.base_url,
        username=args.username,
        password=args.password,
        token=args.token,
        verify=verify
    )

    # 1) Fetch candidate images (we’ll do fine-grained filtering client-side too)
    query = {}
    if args.family: query["family"] = args.family
    if args.version: query["version"] = args.version
    if args.name_contains and not args.name_regex:
        # Some deployments support ?name=exact; substring is handled client-side
        pass

    images = cc.list_images(**query)
    match_fn = compile_filters(argparse.Namespace(
        family=args.family,
        name_contains=args.name_contains,
        name_regex=args.name_regex,
        version=args.version,
        version_regex=args.version_regex,
        type=args.type,
        golden=golden_filter,
        older_than_days=args.older_than_days,
        unused_only=args.unused_only
    ))

    selected = [img for img in images if match_fn(img)]

    # Present selection
    def img_row(img):
        return {
            "imageUuid": img.get("imageUuid") or img.get("id") or img.get("imageId"),
            "name": img.get("name") or img.get("imageName"),
            "version": img.get("version") or img.get("softwareVersion"),
            "family": img.get("family") or img.get("familyName"),
            "type": img.get("imageType") or img.get("type"),
            "golden": bool(img.get("isTaggedGolden") or img.get("isGolden") or img.get("golden")),
            "usedCount": img.get("usedDevicesCount") or img.get("usingDeviceCount") or img.get("deviceCount") or 0
        }

    table = [img_row(i) for i in selected]
    if not args.json:
        log("\nCandidates:")
        for r in table:
            log(f"- {r['imageUuid']}  {r['name']}  v{r['version']}  fam={r['family']}  type={r['type']}  golden={r['golden']}  used={r['usedCount']}")
        log(f"\nTotal matches: {len(table)}")

    if args.dry_run:
        if args.json:
            print(json.dumps({"dry_run": True, "matches": table}, indent=2))
        else:
            log("\nDRY RUN: no deletions performed.")
        return

    if not table:
        log("Nothing to delete (no matches).")
        return

    if not args.yes and not confirm(f"Proceed to delete up to {len(table)} image(s)?"):
        log("Aborted.")
        return

    # Deletions
    deleted, failures = [], []
    limit = args.limit if args.limit and args.limit > 0 else len(table)
    for r in table[:limit]:
        img_id = r["imageUuid"]
        if not img_id:
            failures.append({"row": r, "error": "missing imageUuid"})
            continue

        # Optional: remove golden tag first if parameters provided
        if r["golden"] and args.site_id and args.device_family_identifier and args.device_role:
            log(f"Removing golden tag for image {img_id} ...")
            task_id = cc.remove_golden(args.site_id, args.device_family_identifier, args.device_role, img_id)
            if task_id:
                tr = cc.get_task(task_id)
                if "error" in tr:
                    failures.append({"row": r, "error": f"remove_golden failed: {tr['error']}"})
                    continue

        log(f"Deleting image {img_id} ({r['name']} v{r['version']}) ...")
        res = cc.delete_image(img_id)
        if res.get("ok"):
            task_id = res.get("taskId")
            if task_id:
                tr = cc.get_task(task_id)
                if "error" in tr:
                    failures.append({"row": r, "error": f"task failed: {tr['error']}"})
                    continue
            deleted.append(r)
            log(f"Deleted {img_id}")
        else:
            failures.append({"row": r, "error": f"delete failed (paths tried {res.get('tried')}): {res.get('last_text')}"})

    result = {"deleted": deleted, "failed": failures}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        log(f"\nDone. Deleted: {len(deleted)}, Failed: {len(failures)}")
        if failures:
            log(json.dumps(failures, indent=2))


if __name__ == "__main__":
    main()
