"""Standalone AGPL-3.0 helper; JSON input/output over a subprocess boundary."""

import hashlib
import json
import sys
import time
from pathlib import Path

from weread_client import ACCOUNT_RE, WeReadClient, WeReadError, atomic_json

HOME = Path.home() / "Library/Application Support/WeReadArticleTool"


def login(client, home, args):
    pending = home / "pending-login.json"
    if args.get("action") == "login_start":
        import qrcode

        uid = client.create_login()
        atomic_json(
            pending,
            {
                "uid": uid,
                "created_at": time.time(),
                "cookies": [
                    {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                    for c in client.session.cookies
                ],
            },
        )
        qr = home / "login-qr.png"
        qrcode.make("https://weread.qq.com/web/confirm?uid=" + uid).save(qr)
        qr.chmod(0o600)
        return {
            "status": "login_pending",
            "qr_file": str(qr),
            "message": "请用微信扫码并确认，然后运行 wechat-login --check",
        }
    if not pending.exists():
        return {"status": "login_required", "message": "请先运行 wechat-login 生成二维码"}
    data = json.loads(pending.read_text())
    if time.time() - data["created_at"] > 300:
        return {"status": "login_expired", "message": "二维码已过期，请重新运行 wechat-login"}
    for c in data["cookies"]:
        if c["domain"].lstrip(".") == "weread.qq.com":
            client.session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    result = client.poll_login(data["uid"], args.get("otp", ""))
    if result.get("succeed") is True:
        client.finish_login(result)
        pending.unlink(missing_ok=True)
        (home / "login-qr.png").unlink(missing_ok=True)
        return {"status": "account_resolved", "name": client.name, "message": "登录已保存"}
    code = result.get("logicCode", "pending")
    return {
        "status": "login_pending",
        "code": code,
        "message": "等待手机确认；如需验证码，使用 --otp-file 传入仅包含验证码的本地文件",
    }


def run(args):
    home = Path(args.get("home", HOME))
    client = WeReadClient(home / "login.json")
    if args.get("action", "").startswith("login_"):
        return login(client, home, args)
    if not client.load():
        return {
            "status": "login_required",
            "message": "请运行 agent-reach wechat-login；Agent展示二维码，确认后使用 --check 保存登录",
        }
    client.validate()
    client.renew()
    cache_path = home / "accounts.json"
    cached = json.loads(cache_path.read_text()) if cache_path.exists() else []
    query = args["account"]
    if ACCOUNT_RE.fullmatch(query):
        account = client.account_info(query)
    else:
        candidates = [a for a in cached if a.get("name") == query]
        if not candidates:
            candidates, _ = client.search(query)
            candidates = [a for a in candidates if a["name"] == query]
        if not candidates:
            candidates = [a for a in client.shelf() if a["name"] == query]
        candidates = list({a["id"]: a for a in candidates}.values())
        if len(candidates) != 1:
            return {
                "status": "needs_desktop_account",
                "query": query,
                "candidates": candidates,
                "message": "请Agent在微信读书桌面端核对账号；需要时加入书架，再运行本命令",
            }
        account = client.account_info(candidates[0]["id"])
        if account["name"] != query:
            raise WeReadError("account_mismatch", "账号名称不一致")
    saved = {a["id"]: a for a in cached}
    saved[account["id"]] = {
        **account,
        "source": "live_account_info",
        "verified_at": int(time.time()),
    }
    atomic_json(cache_path, list(saved.values()))
    if args.get("lookup_only"):
        return {"status": "account_resolved", "account": account}
    output = Path(args["output"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "job.json"
    limit = args.get("limit", 20)
    all_available = args.get("all", False)
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("数量须为正整数")
    identity = [account["id"], "all" if all_available else limit]
    if path.exists():
        state = json.loads(path.read_text())
        if state["identity"] != identity:
            raise ValueError("输出目录属于其他任务")
    else:
        catalog = client.catalog(account["id"])
        # This source only proves a first page, not an exhausted history.
        selected = catalog if all_available else catalog[:limit]
        state = {
            "platform": "wechat",
            "identity": identity,
            "account": account,
            "status": "collecting",
            "discovery_complete": False,
            "scope": "微信读书当前返回目录；尚无已验证分页结束证据",
            "selection_fulfilled": not all_available and len(selected) == limit,
            "items": [{**a, "status": "pending"} for a in selected],
        }
        atomic_json(path, state)
    stopped = any(a.get("stop") for a in state["items"])
    for article in state["items"]:
        if article["status"] != "pending":
            continue
        if stopped:
            article.update(status="not_attempted", reason="前序访问拒绝或限流已停止任务")
        else:
            try:
                body = client.article(article)
                text = body["text"]
                dest = output / (article["id"] + ".md")
                dest.write_text(
                    f"# {article['title']}\n\n公众号：{account['name']}\n发布时间：{article.get('published')}\n\n{text}\n"
                )
                article.update(
                    status="body_saved",
                    file=str(dest),
                    characters=len(text),
                    sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),
                )
                time.sleep(1)
            except WeReadError as error:
                stopped = error.code in {
                    "access_denied",
                    "rate_limited",
                    "verification_required",
                    "login_expired",
                    "-2012",
                    "-2041",
                    "redirect",
                }
                article.update(
                    status="failed",
                    error={"code": error.code, "message": error.message},
                    stop=stopped,
                )
        atomic_json(path, state)
    state["body_saved"] = sum(a["status"] in {"body_saved", "complete"} for a in state["items"])
    completed = sum(a["status"] == "complete" for a in state["items"])
    state["status"] = (
        "awaiting_analysis"
        if state["items"] and state["body_saved"] == len(state["items"])
        else "partial"
    )
    if state["items"] and completed == len(state["items"]):
        state["status"] = "complete" if state["selection_fulfilled"] else "partial"
    atomic_json(path, state)
    return state


if __name__ == "__main__":
    try:
        result = run(json.load(sys.stdin))
    except WeReadError as error:
        result = {"status": "blocked", "error": {"code": error.code, "message": error.message}}
    except Exception as error:
        result = {
            "status": "blocked",
            "error": {
                "code": type(error).__name__,
                "message": "读取失败；检查登录、数据格式或依赖",
            },
        }
    print(json.dumps(result, ensure_ascii=False))
