"""Collection CLI adapters; UI discovery and analysis remain with the Agent."""

import json
import subprocess
import sys
from pathlib import Path


def register(sub):
    p = sub.add_parser("collection-frames", help="Append up to eight targeted frames before review")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--video-id", required=True)
    p.add_argument("--seconds", required=True, nargs="+", type=float)
    p = sub.add_parser("wechat-login", help="Renew WeRead login only when needed")
    p.add_argument("--check", action="store_true")
    p.add_argument("--otp-file", type=Path)

    for name in ("collect-wechat", "collect-douyin"):
        p = sub.add_parser(name, help="Resumable collection; Agent performs discovery/review")
        p.add_argument("source", help="WeRead account name/ID or Douyin discovery JSON")
        p.add_argument("--output", required=True, type=Path)
        choice = p.add_mutually_exclusive_group()
        choice.add_argument("--limit", type=int)
        choice.add_argument("--all", action="store_true", dest="all_available")
        if name == "collect-wechat":
            p.add_argument("--lookup-only", action="store_true")
        else:
            p.add_argument(
                "--no-submit", action="store_true", help="Only reuse provided/existing Get records"
            )
    p = sub.add_parser("collection-review", help="Save an Agent-authored evidence-based review")
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--review", required=True, type=Path)
    p = sub.add_parser(
        "collection-status", help="Read saved collection progress without network calls"
    )
    p.add_argument("--output", required=True, type=Path)


def run(args):
    try:
        if args.command in {"collect-wechat", "wechat-login"}:
            payload = (
                {
                    "action": "login_check" if args.check else "login_start",
                    "otp": args.otp_file.read_text().strip() if args.otp_file else "",
                }
                if args.command == "wechat-login"
                else {
                    "account": args.source,
                    "output": str(args.output),
                    "lookup_only": args.lookup_only,
                    "limit": args.limit if args.limit is not None else 20,
                    "all": args.all_available,
                }
            )
            helper = Path(__file__).parent / "weread_helper" / "run.py"
            result = subprocess.run(
                [sys.executable, str(helper)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise ValueError("微信读书辅助程序不可用，请安装 collection 可选依赖")
            state = json.loads(result.stdout)
        elif args.command == "collect-douyin":
            from .jobs import collect

            state = collect(
                json.loads(Path(args.source).read_text()),
                args.output,
                limit=args.limit,
                all_available=args.all_available,
                use_get=not args.no_submit,
            )
        elif args.command == "collection-frames":
            from .frames import supplement

            job = json.loads((args.output / "job.json").read_text())
            item = next(i for i in job["items"] if i["video_id"] == args.video_id)
            state = supplement(item["video_file"], item["frames"], args.seconds)
        elif args.command == "collection-review":
            from .jobs import finalize

            state = finalize(args.output, json.loads(args.review.read_text()))
        else:
            state = json.loads((args.output / "job.json").read_text())
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if state["status"] in {"complete", "awaiting_analysis", "account_resolved"} else 2
    except (ValueError, KeyError, OSError, StopIteration) as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "type": type(error).__name__,
                    "message": str(error)
                    if isinstance(error, ValueError)
                    else "检查任务文件与依赖",
                },
                ensure_ascii=False,
            )
        )
        return 2
