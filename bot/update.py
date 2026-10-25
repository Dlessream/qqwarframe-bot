"""后端更新检查：读取本地提交、对比 GitHub，并监听本地代码变化。"""

from __future__ import annotations

import json
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Any, Dict, Optional

from .net import HttpClient

LOGGER = logging.getLogger(__name__)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SELF_UPDATE_CODE_DIRS = ("bot", "web")
SELF_UPDATE_CODE_FILES = ("run.py", "requirements.txt")
SELF_UPDATE_DATA_FILES = ("component_aliases.json", "frame_aliases.json",
                          "sol_nodes.json")


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
        self.self_update = bool(cfg.get("self_update", True))
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
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "manual": False,
                    "message": "已有更新任务在执行，请稍候。"}
        try:
            return self._apply_update_locked()
        finally:
            self._lock.release()

    def _apply_update_locked(self) -> Dict[str, Any]:
        git = shutil.which("git")
        if not git:
            bundled = os.path.join(ROOT, "tools", "mingit", "cmd", "git.exe")
            if os.path.isfile(bundled):
                git = bundled
        git_dir = os.path.join(ROOT, ".git")
        if git and os.path.exists(git_dir):
            return self._git_update(git)
        if not self.self_update:
            return {"ok": False, "manual": True,
                    "command": self.manual_command,
                    "message": "当前环境已关闭自更新，请在部署机执行更新命令。"}
        return self._archive_update()

    def _git_update(self, git: str) -> Dict[str, Any]:
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

    def _archive_update(self) -> Dict[str, Any]:
        if not self.repo:
            return {"ok": False, "manual": True,
                    "command": self.manual_command,
                    "message": "未配置更新仓库，无法自更新。"}
        url = "https://github.com/%s/archive/refs/heads/%s.tar.gz" % (
            self.repo, self.branch)
        try:
            raw = self.http.request(url, headers={
                "Accept": "application/octet-stream",
                "User-Agent": "QQWarframeBot-Update",
            })
        except Exception as exc:
            return {"ok": False, "manual": True,
                    "command": self.manual_command,
                    "message": "下载最新源码失败：%s" % exc}
        if not raw or len(raw) > 30 * 1024 * 1024:
            return {"ok": False, "manual": True,
                    "command": self.manual_command,
                    "message": "源码包大小异常，已取消更新。"}
        staging = tempfile.mkdtemp(prefix="qqwf-update-")
        backup = None
        try:
            source = self._extract_archive(raw, staging)
            if source is None:
                return {"ok": False, "manual": True,
                        "command": self.manual_command,
                        "message": "源码包结构异常，已取消更新。"}
            backup = os.path.join(ROOT, "logs", "update-backup-%d" % int(time.time()))
            os.makedirs(backup, exist_ok=True)
            for name in SELF_UPDATE_CODE_DIRS + SELF_UPDATE_CODE_FILES:
                current = os.path.join(ROOT, name)
                if os.path.exists(current):
                    shutil.copy2(current, backup) if os.path.isfile(current) else \
                        shutil.copytree(current, os.path.join(backup, name))
            self._install_archive(source, backup)
            commit = self.remote_commit or ""
            if commit:
                with open(os.path.join(ROOT, ".build_commit"), "w",
                          encoding="utf-8") as handle:
                    handle.write(commit + "\n")
                self.local_commit = commit
            self.available = False
            self.state = "up_to_date"
            self.last_error = ""
            self._cleanup_backups()
            return {"ok": True, "manual": False, "restart": True,
                    "message": "源码已更新到 %s，正在重启服务。" % (commit[:7] or "最新版")}
        except Exception as exc:
            self.log.exception("自更新失败")
            if backup:
                try:
                    self._restore_backup(backup)
                except Exception:
                    self.log.exception("回滚失败")
            return {"ok": False, "manual": True,
                    "command": self.manual_command,
                    "message": "自更新失败：%s" % exc}
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _extract_archive(self, raw: bytes, staging: str):
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
                members = tar.getmembers()
                if not members:
                    return None
                root_name = members[0].name.split("/")[0]
                for member in members:
                    if member.issym() or member.islnk():
                        continue
                    parts = member.name.split("/")
                    if not parts or parts[0] != root_name or len(parts) < 2:
                        continue
                    relative = parts[1:]
                    if any(part in ("", ".", "..") for part in relative):
                        continue
                    dest = os.path.join(staging, *relative)
                    if member.isdir():
                        os.makedirs(dest, exist_ok=True)
                        continue
                    if not member.isfile():
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    source = tar.extractfile(member)
                    if source is None:
                        continue
                    with open(dest, "wb") as handle:
                        shutil.copyfileobj(source, handle)
            if not os.path.isfile(os.path.join(staging, "bot", "app.py")):
                return None
            if not os.path.isfile(os.path.join(staging, "run.py")):
                return None
            return staging
        except (tarfile.TarError, OSError, ValueError) as exc:
            self.log.warning("源码包解压失败：%s", exc)
            return None

    def _install_archive(self, source: str, backup: str) -> None:
        for name in SELF_UPDATE_CODE_DIRS:
            new_path = os.path.join(source, name)
            if not os.path.isdir(new_path):
                continue
            target = os.path.join(ROOT, name)
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(new_path, target)
        for name in SELF_UPDATE_CODE_FILES:
            new_path = os.path.join(source, name)
            if os.path.isfile(new_path):
                shutil.copy2(new_path, os.path.join(ROOT, name))
        data_source = os.path.join(source, "data")
        data_target = os.path.join(ROOT, "data")
        os.makedirs(data_target, exist_ok=True)
        for name in SELF_UPDATE_DATA_FILES:
            source_file = os.path.join(data_source, name)
            if os.path.isfile(source_file):
                shutil.copy2(source_file, os.path.join(data_target, name))
        for name in ("default-aliases.json",):
            source_file = os.path.join(source, name)
            if os.path.isfile(source_file):
                shutil.copy2(source_file, os.path.join(ROOT, name))

    def _restore_backup(self, backup: str) -> None:
        for name in SELF_UPDATE_CODE_DIRS:
            saved = os.path.join(backup, name)
            target = os.path.join(ROOT, name)
            if os.path.isdir(saved):
                if os.path.isdir(target):
                    shutil.rmtree(target)
                shutil.copytree(saved, target)
        for name in SELF_UPDATE_CODE_FILES:
            saved = os.path.join(backup, name)
            if os.path.isfile(saved):
                shutil.copy2(saved, os.path.join(ROOT, name))

    def _cleanup_backups(self) -> None:
        logs = os.path.join(ROOT, "logs")
        try:
            backups = sorted(
                (os.path.join(logs, name) for name in os.listdir(logs)
                 if name.startswith("update-backup-")), reverse=True)
        except OSError:
            return
        for path in backups[3:]:
            shutil.rmtree(path, ignore_errors=True)

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
