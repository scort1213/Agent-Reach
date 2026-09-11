import json
from importlib.resources import files
from pathlib import Path

import pytest

from agent_reach.cli import _install_skill


def test_repository_scenarios_match_installed_package_definition():
    repository = Path(__file__).parents[1] / "benchmarks/scenarios.json"
    packaged = files("agent_reach").joinpath("skill/references/benchmark-scenarios.json")
    assert json.loads(repository.read_text(encoding="utf-8")) == json.loads(packaged.read_text(encoding="utf-8"))


@pytest.mark.parametrize("locale", ["zh_CN.UTF-8", "en_US.UTF-8"])
def test_real_skill_install_copies_benchmark_scenarios(tmp_path, monkeypatch, locale):
    skill_root = tmp_path / ".codex" / "skills"
    skill_root.mkdir(parents=True)
    monkeypatch.setattr("os.path.expanduser", lambda value: value.replace("~", str(tmp_path)))
    monkeypatch.delenv("OPENCLAW_HOME", raising=False)
    monkeypatch.setenv("AGENT_REACH_LANG", locale)
    assert _install_skill() is True
    target = skill_root / "agent-reach"
    assert (target / "SKILL.md").is_file()
    references = files("agent_reach").joinpath("skill/references")
    for resource in references.iterdir():
        if resource.is_file() and resource.name.endswith((".md", ".json")):
            assert (target / "references" / resource.name).read_text(encoding="utf-8") == resource.read_text(encoding="utf-8")
    scenarios = json.loads((target / "references/benchmark-scenarios.json").read_text())
    assert len(scenarios) == 18
    assert sum(s["route_group"] == "custom" for s in scenarios) == 3
    assert sum(s["route_group"] == "original" for s in scenarios) == 15
