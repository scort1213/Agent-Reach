"""Bounded public-content probes used by the benchmark case manifests.

The harness records/scrubs output. This module performs no browser discovery,
no credential extraction and no cloud transcription submissions.
"""

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

import feedparser
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["rss", "http", "wechat", "github", "toutiao"])
    parser.add_argument("target")
    args = parser.parse_args()
    kind, target = args.kind, args.target
    if kind == "rss":
        r = requests.get(target, timeout=25)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        print(
            json.dumps(
                {
                    "feed": target,
                    "title": feed.feed.get("title"),
                    "items": [
                        {
                            "title": e.get("title"),
                            "url": e.get("link"),
                            "published": e.get("published"),
                            "summary": e.get("summary"),
                        }
                        for e in feed.entries[:3]
                    ],
                },
                ensure_ascii=False,
            )
        )
    elif kind == "http":
        r = requests.get(target, timeout=25, headers={"User-Agent": "AgentReachBenchmark/1.0"})
        print(json.dumps({"http": r.status_code, "url": r.url, "body": r.text}, ensure_ascii=False))
        r.raise_for_status()
    elif kind == "wechat":
        out = Path.home() / ".agent-reach/benchmarks/probe-artifacts" / str(uuid.uuid4())
        process = subprocess.run(
            ["agent-reach", "collect-wechat", target, "--limit", "3", "--output", str(out)],
            capture_output=True,
            text=True,
            timeout=150,
        )
        try:
            state = json.loads(process.stdout)
            for item in state.get("items", []):
                if item.get("file"):
                    item["saved_body"] = Path(item["file"]).read_text()
            print(json.dumps(state, ensure_ascii=False))
        except ValueError:
            print(process.stdout, process.stderr)
        sys.exit(process.returncode)
    elif kind == "github":
        import base64

        data = {"repo": target, "backend": "public GitHub REST (CLI auth unavailable)"}
        for name, path in [
            ("metadata", ""),
            ("readme", "/readme"),
            ("license", "/license"),
            ("latest_commit", "/commits?per_page=1"),
        ]:
            r = requests.get(
                "https://api.github.com/repos/" + target + path,
                timeout=25,
                headers={"Accept": "application/vnd.github+json"},
            )
            r.raise_for_status()
            d = r.json()
            if name in ["readme", "license"]:
                d = {
                    "path": d["path"],
                    "url": d["html_url"],
                    "text": base64.b64decode(d.get("content", "")).decode(errors="replace"),
                }
            elif name == "metadata":
                d = {
                    k: d.get(k)
                    for k in [
                        "full_name",
                        "description",
                        "html_url",
                        "archived",
                        "pushed_at",
                        "license",
                    ]
                }
            elif name == "latest_commit":
                d = [
                    {
                        "sha": c["sha"],
                        "date": c["commit"]["committer"]["date"],
                        "message": c["commit"]["message"],
                    }
                    for c in d
                ]
            data[name] = d
        print(json.dumps(data, ensure_ascii=False))

    elif kind == "toutiao":
        from agent_reach.readers.toutiao_batch import read_batch

        out = Path.home() / ".agent-reach/benchmarks/probe-artifacts" / str(uuid.uuid4())
        state = read_batch(json.loads(Path(target).read_text()), out, limit=3)
        state["saved_files"] = [str(x) for x in out.glob("*.md")]
        print(json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    main()
