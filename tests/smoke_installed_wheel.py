"""Run with the clean wheel venv's ``python -I``; no credentials or network."""

import importlib
import json
import subprocess
import sys
import sysconfig
import tempfile
from importlib.metadata import distribution
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch


def main():
    version = distribution("agent-reach").version
    assert importlib.import_module("agent_reach").__version__ == version, "runtime version mismatch"
    package = files("agent_reach")
    site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
    assert Path(str(package)).resolve().is_relative_to(site_packages), "loaded source checkout"
    skill = package.joinpath("skill/SKILL.md").read_text(encoding="utf-8")
    references = package.joinpath("skill/references")
    skill += "\n".join(p.read_text(encoding="utf-8") for p in references.iterdir()
                       if p.name.endswith(".md"))
    scenarios = json.loads(package.joinpath("skill/references/benchmark-scenarios.json").read_text(encoding="utf-8"))
    assert len(scenarios) == 18 and len({s["platform"] for s in scenarios}) == 18
    assert sum(s["route_group"] == "custom" for s in scenarios) == 3
    assert sum(s["route_group"] == "original" for s in scenarios) == 15
    for name in ("av", "bs4", "PIL.Image", "qrcode", "agent_reach.collection.podcast"):
        importlib.import_module(name)
    entrypoints = {e.name: e for e in distribution("agent-reach").entry_points}
    with tempfile.TemporaryDirectory(prefix="agent-reach-wheel-smoke-") as directory:
        for name in ("agent-reach", "agent-reach-benchmark", "agent-reach-benchmark-source"):
            assert name in entrypoints
            assert callable(entrypoints[name].load())
            executable = Path(sysconfig.get_path("scripts")) / name
            result = subprocess.run([str(executable), "--help"], capture_output=True,
                                    text=True, check=True, cwd=directory, timeout=20)
            assert "usage:" in result.stdout.lower()
        executable = Path(sysconfig.get_path("scripts")) / "agent-reach"
        result = subprocess.run([str(executable), "version"], capture_output=True,
                                text=True, check=True, cwd=directory, timeout=20)
        assert version in result.stdout, "CLI version mismatch"
        for command in ("collect-wechat", "collect-douyin", "collect-podcast",
                        "collection-status", "collection-review", "read-toutiao-batch",
                        "collect-youtube", "read-x"):
            assert command in skill, f"Skill missing {command}"
            subprocess.run([str(executable), command, "--help"], check=True,
                           stdout=subprocess.DEVNULL, cwd=directory, timeout=20)
        helper = package.joinpath("collection/weread_helper/run.py")
        result = subprocess.run(
            [sys.executable, str(helper)],
            input=json.dumps({"home": str(Path(directory) / "session"), "account": "fixture"}),
            capture_output=True, text=True, cwd=directory, check=True, timeout=20,
        )
        assert json.loads(result.stdout)["status"] == "login_required", result.stdout
        # Exercise the real installer too: package resources existing in the
        # wheel does not prove they reach the agent's installed Skill folder.
        from agent_reach.cli import _install_skill

        isolated_home = Path(directory) / "skill-home"
        (isolated_home / ".codex/skills").mkdir(parents=True)
        with patch("os.path.expanduser", side_effect=lambda p: p.replace("~", str(isolated_home))), \
                patch.dict("os.environ", {"OPENCLAW_HOME": ""}):
            assert _install_skill() is True
        installed = isolated_home / ".codex/skills/agent-reach"
        assert (installed / "SKILL.md").is_file()
        assert json.loads((installed / "references/benchmark-scenarios.json").read_text(encoding="utf-8")) == scenarios
        for reference in references.iterdir():
            if reference.is_file() and reference.name.endswith((".md", ".json")):
                assert (installed / "references" / reference.name).read_text(encoding="utf-8") == reference.read_text(encoding="utf-8")
    print("Installed wheel OK: collection deps, 18 scenarios, Skill, CLI and standalone helper")


if __name__ == "__main__":
    main()
