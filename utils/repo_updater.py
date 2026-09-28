# -*- coding: utf-8 -*-
"""项目仓库自动更新（公开 GitHub + 国内镜像备用）。

原则：
- 仅快进更新（ff-only），不 reset --hard
- 有未提交的已跟踪改动时拒绝更新（未跟踪文件不挡）
- fetch 依次尝试：直连 origin → 配置的国内镜像
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# 公开仓常用镜像（随时可能失效，启动器设置可覆盖）
DEFAULT_MIRROR_TEMPLATES = (
    "https://gitclone.com/{github_path}",
    "https://ghproxy.net/https://{github_path}",
    "https://mirror.ghproxy.com/https://{github_path}",
    "https://ghfast.top/https://{github_path}",
)

_CREATE_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW
    if (os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"))
    else 0
)


@dataclass
class UpdateCheckResult:
    ok: bool
    message: str
    repo_root: str = ""
    branch: str = ""
    local_sha: str = ""
    remote_sha: str = ""
    behind: int = 0
    ahead: int = 0
    dirty: bool = False
    dirty_detail: str = ""
    fetch_url: str = ""
    can_update: bool = False
    updated: bool = False
    tried: List[str] = field(default_factory=list)


def _run_git(
    repo_root: str,
    args: Sequence[str],
    *,
    timeout: int = 90,
) -> Tuple[int, str, str]:
    git = shutil.which("git")
    if not git:
        return 127, "", "未找到 git，请先安装 Git for Windows 并加入 PATH"
    try:
        cp = subprocess.run(
            [git, *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return int(cp.returncode or 0), (cp.stdout or "").strip(), (cp.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "git 命令超时"
    except Exception as e:
        return 1, "", str(e)


def find_repo_root(start: Optional[str] = None) -> Optional[str]:
    """从 start 向上找含 .git 的目录。"""
    cur = os.path.abspath(start or os.getcwd())
    for _ in range(8):
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(
            os.path.join(cur, ".git")
        ):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    code, out, _ = _run_git(start or os.getcwd(), ["rev-parse", "--show-toplevel"], timeout=15)
    if code == 0 and out:
        return out.replace("/", os.sep)
    return None


def _normalize_github_https(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("git@"):
        # git@github.com:user/repo.git
        m = re.match(r"git@([^:]+):(.+)$", u)
        if m:
            host, path = m.group(1), m.group(2)
            if not path.endswith(".git"):
                path += ".git"
            return f"https://{host}/{path}"
    u = u.rstrip("/")
    return u


def _github_path(url: str) -> str:
    """https://github.com/a/b.git → github.com/a/b.git"""
    u = _normalize_github_https(url)
    u = re.sub(r"^https?://", "", u, flags=re.I)
    return u


def build_fetch_candidates(
    origin_url: str,
    mirror_templates: Optional[Sequence[str]] = None,
) -> List[str]:
    """直连 + 镜像候选列表（去重保序）。"""
    origin = _normalize_github_https(origin_url)
    out: List[str] = []
    seen = set()

    def _add(u: str) -> None:
        u = (u or "").strip()
        if not u or u in seen:
            return
        seen.add(u)
        out.append(u)

    if origin:
        _add(origin)
    path = _github_path(origin) if origin else ""
    templates = list(mirror_templates or DEFAULT_MIRROR_TEMPLATES)
    if path:
        for tpl in templates:
            try:
                _add(str(tpl).format(github_path=path, origin=origin))
            except Exception:
                continue
    return out


def _current_branch(repo_root: str) -> str:
    code, out, _ = _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=15)
    if code == 0 and out and out != "HEAD":
        return out
    return "main"


def _origin_url(repo_root: str) -> str:
    code, out, _ = _run_git(repo_root, ["remote", "get-url", "origin"], timeout=15)
    return out if code == 0 else ""


def _sha(repo_root: str, ref: str) -> str:
    code, out, _ = _run_git(repo_root, ["rev-parse", ref], timeout=15)
    return out if code == 0 else ""


def _dirty_tracked(repo_root: str) -> Tuple[bool, str]:
    """已跟踪文件有改动/暂存/冲突则视为脏；仅未跟踪文件不算。"""
    code, out, err = _run_git(repo_root, ["status", "--porcelain"], timeout=30)
    if code != 0:
        return True, err or "无法读取 git status"
    lines = []
    for line in (out or "").splitlines():
        if not line:
            continue
        if line.startswith("??"):
            continue
        lines.append(line)
    if not lines:
        return False, ""
    preview = "\n".join(lines[:8])
    if len(lines) > 8:
        preview += f"\n…共 {len(lines)} 处"
    return True, preview


def _ahead_behind(repo_root: str, local_ref: str, remote_ref: str) -> Tuple[int, int]:
    code, out, _ = _run_git(
        repo_root,
        ["rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}"],
        timeout=30,
    )
    if code != 0 or not out:
        return 0, 0
    parts = out.replace("\t", " ").split()
    if len(parts) >= 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return 0, 0
    return 0, 0


def check_for_updates(
    project_root: str,
    *,
    mirror_templates: Optional[Sequence[str]] = None,
    fetch_timeout: int = 60,
) -> UpdateCheckResult:
    repo = find_repo_root(project_root)
    if not repo:
        return UpdateCheckResult(False, "当前目录不是 git 仓库，无法自动更新。", repo_root=project_root)

    if not shutil.which("git"):
        return UpdateCheckResult(False, "未安装 git 或不在 PATH 中。", repo_root=repo)

    branch = _current_branch(repo)
    origin = _origin_url(repo)
    if not origin:
        return UpdateCheckResult(
            False, "未配置 origin 远程地址。", repo_root=repo, branch=branch
        )

    dirty, dirty_detail = _dirty_tracked(repo)
    local_sha = _sha(repo, "HEAD")
    candidates = build_fetch_candidates(origin, mirror_templates)
    tried: List[str] = []
    fetch_ok_url = ""
    last_err = ""

    for url in candidates:
        tried.append(url)
        # 把远端分支写到 origin/<branch>，不改本地工作区
        code, _out, err = _run_git(
            repo,
            [
                "fetch",
                "--prune",
                url,
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            ],
            timeout=fetch_timeout,
        )
        if code == 0:
            fetch_ok_url = url
            break
        last_err = err or f"exit {code}"

    if not fetch_ok_url:
        return UpdateCheckResult(
            False,
            f"无法从 GitHub/镜像拉取远程信息。\n最后错误：{last_err}",
            repo_root=repo,
            branch=branch,
            local_sha=local_sha,
            dirty=dirty,
            dirty_detail=dirty_detail,
            tried=tried,
        )

    remote_ref = f"origin/{branch}"
    remote_sha = _sha(repo, remote_ref)
    if not remote_sha:
        return UpdateCheckResult(
            False,
            f"已连接镜像，但未找到远程分支 origin/{branch}。",
            repo_root=repo,
            branch=branch,
            local_sha=local_sha,
            fetch_url=fetch_ok_url,
            dirty=dirty,
            dirty_detail=dirty_detail,
            tried=tried,
        )

    ahead, behind = _ahead_behind(repo, "HEAD", remote_ref)

    if behind <= 0 and ahead <= 0:
        msg = "已是最新。"
        can = False
    elif behind > 0 and ahead > 0:
        msg = (
            f"本地与远程已分叉（落后 {behind} / 超前 {ahead}），"
            f"请勿自动覆盖，请手动处理后再更新。"
        )
        can = False
    elif ahead > 0:
        msg = f"本地比远程超前 {ahead} 个提交，跳过自动更新。"
        can = False
    else:
        msg = f"发现远程有 {behind} 个新提交。"
        can = not dirty

    if dirty and behind > 0:
        msg += "\n本地有未提交修改，已禁止自动更新（避免覆盖）。"
        if dirty_detail:
            msg += f"\n{dirty_detail}"

    return UpdateCheckResult(
        True,
        msg,
        repo_root=repo,
        branch=branch,
        local_sha=local_sha,
        remote_sha=remote_sha,
        behind=behind,
        ahead=ahead,
        dirty=dirty,
        dirty_detail=dirty_detail,
        fetch_url=fetch_ok_url,
        can_update=can and behind > 0,
        tried=tried,
    )


def apply_fast_forward_update(
    project_root: str,
    *,
    mirror_templates: Optional[Sequence[str]] = None,
) -> UpdateCheckResult:
    """检查并快进合并到 origin/<branch>。"""
    chk = check_for_updates(project_root, mirror_templates=mirror_templates)
    if not chk.ok:
        return chk
    if chk.behind <= 0:
        chk.can_update = False
        chk.message = "已是最新，无需更新。"
        return chk
    if chk.dirty:
        chk.can_update = False
        chk.message = (
            "本地有未提交修改，已取消更新。\n"
            "请先提交/备份/还原本地改动后再点「检查更新」。"
        )
        if chk.dirty_detail:
            chk.message += f"\n{chk.dirty_detail}"
        return chk
    if chk.ahead > 0:
        chk.can_update = False
        chk.message = "本地与远程分叉或超前，已取消自动更新。"
        return chk

    remote_ref = f"origin/{chk.branch}"
    code, _out, err = _run_git(
        chk.repo_root,
        ["merge", "--ff-only", remote_ref],
        timeout=120,
    )
    if code != 0:
        chk.ok = False
        chk.can_update = False
        chk.message = f"快进合并失败：{err or code}"
        return chk

    new_sha = _sha(chk.repo_root, "HEAD")
    chk.local_sha = new_sha
    chk.behind = 0
    chk.can_update = False
    chk.updated = True
    chk.message = (
        f"已更新到最新（{new_sha[:10]}）。\n"
        f"拉取通道：{chk.fetch_url}\n"
        f"请重启已打开的交易/策略等子系统；若改了 QMT 脚本请再部署并重跑策略。"
    )
    return chk
