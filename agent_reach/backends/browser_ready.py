"""Explicit, bounded preparation. Doctor remains a read-only observer."""

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode

from .opencli import _fetch_daemon_status


def prepare(profile=None, wait=0):
    if not 0 <= wait <= 45:
        raise ValueError("等待时间须为0—45秒")
    config_path = Path.home() / ".opencli/browser-profiles.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    requested = profile or config.get("defaultContextId")
    context = config.get("aliases", {}).get(requested, requested)
    if not isinstance(context, str) or not context:
        return {"status": "needs_profile", "message": "先核对并选择 OpenCLI 浏览器资料"}
    actions = []
    status = _fetch_daemon_status()
    if status is None:
        # Upstream doctor ensures startup without restarting an active daemon.
        env = os.environ.copy()
        env.pop("OPENCLI_DAEMON_PORT", None)
        try:
            subprocess.run(["opencli", "doctor"], env=env, capture_output=True, timeout=20,
                           check=False)
            actions.append("upstream_startup_requested")
        except (OSError, subprocess.TimeoutExpired):
            return {"status": "daemon_unavailable", "profile": context, "actions": actions}
    deadline = time.monotonic() + wait
    while True:
        status = _fetch_daemon_status(query=urlencode({"contextId": context}))
        if (status and status.get("extensionConnected") is True
                and status.get("contextId") == context):
            return {"status": "ready", "profile": context, "actions": actions,
                    "pending": status.get("pending", 0),
                    "command_result_unknown": status.get("commandResultUnknown", 0)}
        if time.monotonic() >= deadline:
            return {"status": "needs_browser", "profile": context, "actions": actions,
                    "message": "Agent打开对应Chrome资料并确认扩展启用，再以 --wait 45 续查；不得换账号",
                    "retry_after_browser_open": True}
        time.sleep(min(1, max(0, deadline - time.monotonic())))
