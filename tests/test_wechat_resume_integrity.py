import importlib.util
import json
from pathlib import Path

import pytest

HELPER = Path(__file__).parents[1] / "agent_reach/collection/weread_helper"


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    spec = importlib.util.spec_from_file_location("wechat_resume_helper", HELPER / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    return mod


class Client:
    trace = []
    calls = 0

    def load(self):
        return True

    def prepare(self):
        pass

    def account_info(self, value):
        return {"id": value, "name": "fixture"}

    def catalog(self, value):
        return [
            {"id": "one", "title": "one"},
            {"id": "one", "title": "duplicate"},
            {"id": "two", "title": "two"},
        ]

    def article(self, value):
        self.calls += 1
        return {"text": "完整测试正文，不是真实网络内容"}


def args(tmp_path, **kw):
    return {
        "home": str(tmp_path),
        "output": str(tmp_path / "out"),
        "account": "MP_WXS_123",
        "limit": 1,
        **kw,
    }


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_resume_does_not_claim_missing_or_changed_body(helper, tmp_path, damage):
    c = Client()
    state = helper._run(args(tmp_path), c)
    body = Path(state["items"][0]["file"])
    if damage == "missing":
        body.unlink()
    else:
        body.write_text("用户编辑")
    state = helper._run(args(tmp_path), c)
    assert state["status"] == "partial"
    assert state["body_saved"] == 0
    assert state["items"][0]["error"]["code"] == "saved_body_changed_or_missing"
    assert c.calls == 1
    if damage == "changed":
        assert body.read_text() == "用户编辑"


def test_missing_report_returns_to_analysis_without_refetch(helper, tmp_path):
    c = Client()
    state = helper._run(args(tmp_path), c)
    state["items"][0].update(status="complete", report=str(tmp_path / "missing.md"))
    state["completed"] = 1
    (tmp_path / "out/job.json").write_text(json.dumps(state))
    state = helper._run(args(tmp_path), c)
    assert state["status"] == "awaiting_analysis"
    assert state["completed"] == 0
    assert c.calls == 1


def test_catalog_dedup_before_limit_and_all_not_history(helper, tmp_path):
    state = helper._run(args(tmp_path, limit=2), Client())
    assert [x["id"] for x in state["items"]] == ["one", "two"]
    assert state["selection_fulfilled"]
    state = helper._run(args(tmp_path, **{"output": str(tmp_path / "all"), "all": True}), Client())
    assert not state["selection_fulfilled"]
    assert not state["discovery_complete"]
