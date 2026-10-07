"""Remove only the obsolete, exclusively tagged ReelVault 0.1.0-rc.1 image.

The repository workflow supplies its short-lived GITHUB_TOKEN. No PAT is read,
and neither the package nor the target tag can be changed through inputs.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPOSITORY = "Jelatine/ReelVault"
PACKAGE = "/users/Jelatine/packages/container/reelvault/versions"
TARGET = "0.1.0-rc.1"


def candidate(versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [v for v in versions if TARGET in v["metadata"]["container"]["tags"]]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError("Multiple versions contain the test tag; refusing deletion")
    version = matches[0]
    if set(version["metadata"]["container"]["tags"]) != {TARGET}:
        raise RuntimeError("The test version also holds other tags; refusing deletion")
    if not isinstance(version["id"], int) or isinstance(version["id"], bool) or version["id"] <= 0:
        raise RuntimeError("Invalid package version identifier")
    return version


def protected_tags(versions: list[dict[str, Any]]) -> dict[str, tuple[int, str]]:
    result = {}
    for version in versions:
        for tag in version["metadata"]["container"]["tags"]:
            if tag == TARGET:
                continue
            if tag in result:
                raise RuntimeError("Duplicate protected tag; refusing an ambiguous snapshot")
            result[tag] = (version["id"], version["name"])
    return result


class Registry:
    def __init__(self, token: str) -> None:
        self.token = token

    def request(self, path: str, method: str = "GET") -> Any:
        request = urllib.request.Request(
            "https://api.github.com" + path,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            return json.loads(data) if data else None

    def versions(self) -> list[dict[str, Any]]:
        versions = []
        for page in range(1, 101):
            batch = self.request(f"{PACKAGE}?state=active&per_page=100&page={page}")
            if not isinstance(batch, list):
                raise RuntimeError("Invalid package version response")
            versions.extend(batch)
            if len(batch) < 100:
                return versions
        raise RuntimeError("Version scan exceeded its limit; refusing an incomplete result")


def cleanup(registry: Registry, *, delete: bool = False) -> dict[str, Any]:
    versions = registry.versions()
    protected = protected_tags(versions)
    try:
        target = candidate(versions)
    except RuntimeError as error:
        if delete:
            raise
        return {
            "status": "refused",
            "tag": TARGET,
            "reason": str(error),
            "matches": [
                {"id": v["id"], "digest": v["name"], "tags": v["metadata"]["container"]["tags"]}
                for v in versions
                if TARGET in v["metadata"]["container"]["tags"]
            ],
        }
    if target is None:
        return {"status": "already_absent", "tag": TARGET, "protected_tags": len(protected)}
    report = {
        "status": "dry_run",
        "tag": TARGET,
        "version_id": target["id"],
        "digest": target["name"],
        "protected_tags": len(protected),
    }
    if not delete:
        return report
    # Reread the exact immutable version immediately before deleting it.
    fresh = registry.request(f"{PACKAGE}/{target['id']}")
    checked = candidate([fresh])
    if checked is None or checked["id"] != target["id"] or checked["name"] != target["name"]:
        raise RuntimeError("The target changed after inspection; refusing deletion")
    registry.request(f"{PACKAGE}/{target['id']}", "DELETE")
    for _ in range(15):
        current = registry.versions()
        after = protected_tags(current)
        if any(after.get(tag) != value for tag, value in protected.items()):
            raise RuntimeError("Protected tags changed during cleanup; inspect package history")
        if not any(TARGET in v["metadata"]["container"]["tags"] for v in current):
            report["status"] = "deleted_and_verified"
            return report
        time.sleep(2)
    raise RuntimeError("Deletion was accepted but tag absence is not yet verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete", action="store_true", help="delete the exclusively tagged test version"
    )
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise SystemExit("This cleanup is restricted to the Jelatine/ReelVault repository")
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("The workflow token is required")
    report = cleanup(Registry(token), delete=args.delete)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as stream:
            stream.write(f"### Obsolete test image cleanup\n\n```json\n{output}\n```\n")


if __name__ == "__main__":
    main()
