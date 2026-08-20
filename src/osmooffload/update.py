"""Update check against GitHub releases (stdlib only, no telemetry).

One GET to api.github.com/repos/<repo>/releases/latest. Disabled until a
repo is configured. Network failures return None silently — an update check
must never bother the user.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("osmo.update")


@dataclass
class UpdateInfo:
    latest: str
    url: str


def _ver_tuple(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums[:4]) or (0,)


def check(current: str, repo: str, timeout: float = 8.0) -> UpdateInfo | None:
    repo = (repo or "").strip().strip("/")
    if not repo or repo.count("/") != 1:
        return None
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"User-Agent": "OsmoOffload-update-check",
                     "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = str(data.get("tag_name") or "").lstrip("vV")
        url = str(data.get("html_url") or f"https://github.com/{repo}/releases")
        if tag and _ver_tuple(tag) > _ver_tuple(current):
            return UpdateInfo(latest=tag, url=url)
    except Exception as e:
        log.debug("update check failed: %s", e)
    return None
