"""后端更新检查：读取本地提交、对比 GitHub，并监听本地代码变化。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, Optional

from .net import HttpClient

LOGGER = logging.getLogger(__name__)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_git_head(root: str) -> str:
    git_path = os.path.join(root, ".git")
    if not os.path.exists(git_path):
        return ""
    git_dir = git_path
    if os.path.isfile(git_path):
        try:
            with open(git_path, encoding="utf-8") as handle:
                line = handle.read().strip()
            if line.startswith("gitdir:"):
                git_dir = line.split(":", 1)[1].strip()
                if not os.path.isabs(git_dir):
                    git_dir = os.path.join(root, git_dir)
        except OSError:
            return ""
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as handle:
            head = handle.read().strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        try:
            with open(os.path.join(git_dir, ref), encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            pass
        try:
            with open(os.path.join(git_dir, "packed-refs"), encoding="utf-8") as handle:
                for line in handle:
                    parts = line.strip().split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
        except OSError:
            pass
        return ""
    return head if re.fullmatch(r"[0-9a-fA-F]{7,64}", head) else ""


def read_local_commit(root: str = ROOT) -> str:
    env_value = os.environ.get("QQWF_COMMIT", "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", env_value):
        return env_value
    git_commit = _read_git_head(root)
    if git_commit:
        return git_commit
    for name in (".build_commit", "BUILD_COMMIT"):
        try:
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                value = handle.read().strip()
            if re.fullmatch(r"[0-9a-fA-F]{7,64}", value):
                return value
        except OSError:
            continue
    return ""


def _watch_mtime(root: str = ROOT) -> float:
    latest = 0.0
    paths = [os.path.join(root, "bot"), os.path.join(root, "run.py")]
    for path in paths:
        if os.path.isfile(path):
            latest = max(latest, os.path.getmtime(path))
            continue
        if not os.path.isdir(path):
            continue
        for dirpath, _dirnames, filenames in os.walk(path):
            if "__pycache__" in dirpath:
                continue
            for name in filenames:
                if name.endswith(".py"):
                    try:
                        latest = max(latest, os.path.getmtime(
                            os.path.join(dirpath, name)))
                    except OSError:
                        pass
    return latest


class UpdateChecker:
    def __init__(self, config: Dict[str, Any],
                 proxy: str = "",
                 logger: Optional[logging.Logger] = None,
                 on_notify=None):
        cfg = dict(config or {})
        self.repo = str(cfg.get("repo") or "").strip()
        self.branch = str(cfg.get("branch") or "main").strip()
        self.interval = max(60, int(cfg.get("check_seconds") or 600))
        self.manual_command = str(cfg.get("manual_command") or "").strip()
        self.log = logger or LOGGER
        self.on_notify = on_notify
        self.local_commit = read_local_commit()
        self.remote_commit = ""
        self.remote_message = ""
        self.remote_date = ""
        self.remote_url = ""
        self.last_checked = 0.0
        self.last_error = ""
        self.available = False
        self.state = "unknown"
        self.relation = ""
        self.local_changed = False
        self._local_baseline = _watch_mtime()
        self._local_stamp = self._local_baseline
        self._notified_remote = ""
        self._notified_local = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.http = HttpClient(
            timeout=12, retries=1,
            proxy=str(proxy or cfg.get("proxy") or "none"),
            logger=self.log)
        if not self.local_commit:
            self.state = "unknown"
            self.last_error = ("本地版本未知：缺少 .git 或 .build_commit。"
                               "Docker 请带 BUILD_COMMIT 重建，"
                               "或设置 QQWF_COMMIT 环境变量。")

    def start(self) -> None:
        if not self.repo:
            self.log.warning("未配置更新仓库，Web 更新检查关闭")
            return
        self._local_baseline = _watch_mtime()
        self._local_stamp = self._local_baseline
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="update-check",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_local()
                if time.time() - self.last_checked >= self.interval:
                    self.check_remote()
            except Exception as exc:  # pragma: no cover - 防御
                self.log.warning("更新检查异常：%s", exc)
            self._stop.wait(5)

    def check_local(self) -> bool:
        latest = _watch_mtime()
        changed = latest > self._local_baseline + 1.0
        if changed and not self.local_changed:
            self.local_changed = True
            self._local_stamp = latest
            self.log.info("检测到后端代码变化，等待确认重启")
            self._notify("local")
        return changed

    def check_remote(self) -> Dict[str, Any]:
        if not self.repo:
            return self.snapshot()
        url = "https://api.github.com/repos/%s/commits/%s" % (
            self.repo, self.branch)
        try:
            raw = self.http.request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "QQWarframeBot-UpdateChecker",
            })
            data = json.loads(raw.decode("utf-8"))
            commit = data.get("commit") or {}
            self.remote_commit = str(data.get("sha") or "")
            self.remote_message = str(commit.get("message") or "").splitlines()[0]
            self.remote_date = str((commit.get("committer") or {}).get("date") or "")
            self.remote_url = str(data.get("html_url") or "")
            if not self.local_commit:
                self.available = False
                self.relation = ""
                self.state = "local_unknown"
                self.last_error = (
                    "本地版本未知（缺少 .git/.build_commit）；"
                    "远端最新 %s。Docker 请带 BUILD_COMMIT 重建，"
                    "或设置 QQWF_COMMIT 环境变量。" % self.remote_commit[:7])
                self.last_checked = time.time()
                return self.snapshot()
            self.available = bool(
                self.local_commit and self.remote_commit
                and self.local_commit != self.remote_commit)
            self.relation = ""
            if self.available:
                # 只有远端确实领先时才提示更新，避免把本地未推送的提交当成“可更新”。
                compare_url = (
                    "https://api.github.com/repos/%s/compare/%s...%s"
                    % (self.repo, self.local_commit, self.remote_commit))
                try:
                    compare_raw = self.http.request(compare_url, headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "QQWarframeBot-UpdateChecker",
                    })
                    compare = json.loads(compare_raw.decode("utf-8"))
                    self.relation = str(compare.get("status") or "")
                    if self.relation not in ("ahead", "diverged"):
                        self.available = False
                except Exception as exc:
                    # 本地提交不在远端（404）或网络失败时，不提示自动更新。
                    self.available = False
                    self.relation = "unknown"
                    if "404" not in str(exc):
                        self.last_error = "无法比较本地/远端提交：%s" % exc
            self.state = "available" if self.available else (
                "up_to_date" if self.local_commit else "unknown")
            if self.local_commit and self.remote_commit and not self.available \
                    and self.local_commit != self.remote_commit:
                if self.relation == "behind":
                    self.state = "local_ahead"
                elif self.relation == "diverged":
                    self.state = "diverged"
                else:
                    self.state = "local_only"
            self.last_error = "" if self.local_commit else "未找到本地提交信息"
        except Exception as exc:
            self.last_error = "检查更新失败：%s" % exc
            self.state = "error"
            self.log.warning(self.last_error)
        self.last_checked = time.time()
        if self.available and self.remote_commit != self._notified_remote:
            self._notified_remote = self.remote_commit
            self._notify("remote")
        return self.snapshot()

    def check_now(self) -> Dict[str, Any]:
        self.check_local()
        return self.check_remote()

    def acknowledge_local(self) -> None:
        self.local_changed = False
        self._local_baseline = _watch_mtime()

    def dismiss_remote(self) -> None:
        self._notified_remote = self.remote_commit

    def apply_update(self) -> Dict[str, Any]:
        git = shutil.which("git")
        if not git:
            bundled = os.path.join(ROOT, "tools", "mingit", "cmd", "git.exe")
            if os.path.isfile(bundled):
                git = bundled
        git_dir = os.path.join(ROOT, ".git")
        if not git or not os.path.exists(git_dir):
            return {
                "ok": False,
                "manual": True,
                "command": self.manual_command,
                "message": "当前是发布包/容器环境，请在部署机执行更新命令。",
            }
        try:
            result = subprocess.run(
                [git, "-C", ROOT, "pull", "--ff-only"],
                capture_output=True, text=True, timeout=120)
        except Exception as exc:
            return {"ok": False, "manual": False,
                    "message": "执行 git pull 失败：%s" % exc}
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return {"ok": False, "manual": False, "output": output[-2000:],
                    "message": "git pull 失败，请手动处理冲突。"}
        self.local_commit = read_local_commit()
        self.available = False
        self.state = "up_to_date"
        return {"ok": True, "manual": False, "restart": True,
                "output": output[-2000:],
                "message": "代码已更新，正在重启服务。"}

    def _notify(self, kind: str) -> None:
        if not self.on_notify:
            return
        try:
            self.on_notify(self.snapshot(), kind)
        except Exception as exc:  # pragma: no cover - 防御
            self.log.warning("发送更新提示失败：%s", exc)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "local_commit": self.local_commit,
            "local_short": self.local_commit[:7],
            "local_missing": not bool(self.local_commit),
            "remote_commit": self.remote_commit,
            "remote_short": self.remote_commit[:7],
            "remote_message": self.remote_message,
            "remote_date": self.remote_date,
            "remote_url": self.remote_url,
            "available": self.available,
            "local_changed": self.local_changed,
            "state": self.state,
            "relation": self.relation,
            "last_checked": self.last_checked,
            "last_error": self.last_error,
            "check_seconds": self.interval,
            "manual_command": self.manual_command,
            "repo": self.repo,
            "branch": self.branch,
        }
