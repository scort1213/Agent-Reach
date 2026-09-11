# -*- coding: utf-8 -*-
"""Xiaoyuzhou Podcast (小宇宙播客) — public pages + Get, with the legacy Groq route retained."""

import importlib.util
import json
import os
from pathlib import Path

from agent_reach.config import Config
from agent_reach.probe import probe_command

from .base import Channel


def get_configured():
    path = Path.home() / 'Library/Application Support/AgentReachGetNote/credentials.json'
    try:
        data = json.loads(path.read_text())
        return (all(isinstance(data.get(k), str) and data[k] for k in ('api_key', 'client_id'))
                and importlib.util.find_spec('bs4') is not None)
    except (OSError, ValueError, TypeError):
        return False


class XiaoyuzhouChannel(Channel):
    name = "xiaoyuzhou"
    description = "小宇宙播客转文字"
    backends = ["public-get", "groq-whisper", "ffmpeg"]
    tier = 1

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "xiaoyuzhoufm.com")

    def check(self, config=None):
        self.active_backend = None
        if get_configured():
            return "warn", ("Get配置和公开采集入口已就绪：agent-reach collect-podcast URL --output TASK；"
                            "doctor未发起付费转写，实际正文与分析须按任务验收，无需小宇宙App登录")

        # Check ffmpeg — really execute it: a stale pip-installed ffmpeg shim
        # passes shutil.which() but cannot run
        probe = probe_command("ffmpeg", ["-version"], timeout=10, package="ffmpeg")
        if probe.status == "missing":
            return "off", (
                "需要 ffmpeg（音频转码和切片）。安装：\n"
                "  Ubuntu/Debian: apt install -y ffmpeg\n"
                "  macOS: brew install ffmpeg"
            )
        if not probe.ok:
            return "error", (
                "ffmpeg 无法执行，重装：brew install ffmpeg（macOS）/ apt install ffmpeg（Linux）"
            )

        # Check script exists
        script = os.path.expanduser("~/.agent-reach/tools/xiaoyuzhou/transcribe.sh")
        if not os.path.isfile(script):
            return "off", (
                "转录脚本未安装。运行：\n"
                "  agent-reach install --env=auto --system --channels=xiaoyuzhou\n"
                "  或手动复制 transcribe.sh 到 ~/.agent-reach/tools/xiaoyuzhou/"
            )

        # Check GROQ_API_KEY — prefer env var, fall back to Agent Reach config
        has_key = bool(os.environ.get("GROQ_API_KEY"))
        if not has_key:
            try:
                cfg = config if config is not None else Config()
                has_key = bool(cfg.get("groq_api_key"))
            except Exception:
                has_key = False
        if not has_key:
            return "warn", (
                "需要配置 Groq API Key（免费）。步骤：\n"
                "  1. 注册 https://console.groq.com\n"
                "  2. 运行: agent-reach configure groq-key（隐藏输入）"
            )

        self.active_backend = "groq-whisper"
        return "ok", "完整可用（播客下载 + Whisper 转录）"
