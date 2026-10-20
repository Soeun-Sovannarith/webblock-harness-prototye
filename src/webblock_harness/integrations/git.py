"""Version-control integration.

``GitProvider`` is the only way the harness touches client repositories. All
credentials stay server-side inside the provider; nothing here is ever returned
through the API. Branch updates are compare-and-swap (non-force), so a concurrent
push makes the harness fail loudly instead of overwriting someone's work.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from webblock_harness.config import Settings
from webblock_harness.logging_utils import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class RepoInfo:
    owner: str
    name: str
    default_branch: str
    archived: bool = False


@dataclass(frozen=True)
class FileBlob:
    content: str
    sha: str


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    author_name: str
    author_email: str
    message: str
    date: str


@dataclass(frozen=True)
class FileWrite:
    path: str
    content: str | None  # None deletes the file


@dataclass(frozen=True)
class CheckRun:
    name: str
    status: str  # queued | in_progress | completed
    conclusion: str | None  # success | failure | neutral | cancelled | timed_out | action_required | skipped
    url: str | None = None
    summary: str | None = None


class GitError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class StaleRefError(GitError):
    def __init__(self, message: str) -> None:
        super().__init__("stale_ref", message, retryable=False)


class GitProvider(Protocol):
    def get_repo(self, repo: RepoRef) -> RepoInfo | None: ...
    def get_branch_head(self, repo: RepoRef, branch: str) -> str | None: ...
    def list_files(self, repo: RepoRef, ref: str) -> list[str]: ...
    def read_file(self, repo: RepoRef, path: str, ref: str) -> FileBlob | None: ...
    def file_history(self, repo: RepoRef, path: str, ref: str, limit: int = 5) -> list[CommitInfo]: ...
    def changed_files(self, repo: RepoRef, base_sha: str, head_sha: str) -> list[str]: ...
    def commit_message(self, repo: RepoRef, sha: str) -> str: ...
    def commit_files(
        self,
        repo: RepoRef,
        branch: str,
        parent_sha: str,
        writes: list[FileWrite],
        message: str,
        *,
        create_branch: bool,
        reset_job_branch: bool = False,
    ) -> str: ...
    def restore_tree(self, repo: RepoRef, branch: str, parent_sha: str, tree_from_sha: str, message: str) -> str: ...
    def open_pull_request(self, repo: RepoRef, head: str, base: str, title: str, body: str) -> int: ...
    def merge_pull_request(self, repo: RepoRef, number: int, expected_head_sha: str, title: str) -> str: ...
    def check_runs(self, repo: RepoRef, sha: str) -> list[CheckRun]: ...
    def create_repository(self, repo: RepoRef, description: str, private: bool) -> RepoInfo: ...
    def contains_commit(self, repo: RepoRef, branch: str, sha: str) -> bool: ...


def blob_sha(content: str) -> str:
    """Git blob SHA-1 of text content (matches what GitHub reports)."""
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()  # noqa: S324 - git object id


# --------------------------------------------------------------------------
# GitHub (REST v3, Git Data API — no local clone or shell commands required)
# --------------------------------------------------------------------------


class GitHubProvider:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        if settings.github_token is None:
            raise GitError("github_not_configured", "GITHUB_TOKEN / GITHUB_ORGANIZATION_KEY is not set.")
        self._settings = settings
        self._http = client or httpx.Client(
            base_url=settings.github_api_url,
            headers={
                "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "webblock-harness",
            },
            timeout=30.0,
        )

    # -- low level --------------------------------------------------------
    def _req(self, method: str, url: str, *, ok: tuple[int, ...] = (200, 201), **kwargs: Any) -> httpx.Response:
        try:
            resp = self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise GitError("github_unreachable", f"GitHub request failed: {exc.__class__.__name__}", retryable=True) from exc
        if resp.status_code in ok:
            return resp
        retryable = resp.status_code in (429, 502, 503, 504) or (
            resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"
        )
        message = (
            resp.json().get("message", resp.text[:200])
            if resp.headers.get("content-type", "").startswith("application/json")
            else resp.text[:200]
        )
        raise GitError(f"github_{resp.status_code}", f"GitHub {method} {url} -> {resp.status_code}: {message}", retryable)

    def _repo(self, repo: RepoRef) -> str:
        return f"/repos/{repo.owner}/{repo.name}"

    # -- reads ------------------------------------------------------------
    def get_repo(self, repo: RepoRef) -> RepoInfo | None:
        resp = self._req("GET", self._repo(repo), ok=(200, 404))
        if resp.status_code == 404:
            return None
        data = resp.json()
        return RepoInfo(
            owner=data["owner"]["login"],
            name=data["name"],
            default_branch=data["default_branch"],
            archived=bool(data.get("archived")),
        )

    def get_branch_head(self, repo: RepoRef, branch: str) -> str | None:
        resp = self._req("GET", f"{self._repo(repo)}/git/ref/heads/{branch}", ok=(200, 404))
        return None if resp.status_code == 404 else str(resp.json()["object"]["sha"])

    def list_files(self, repo: RepoRef, ref: str) -> list[str]:
        resp = self._req("GET", f"{self._repo(repo)}/git/trees/{ref}", params={"recursive": "1"})
        return sorted(e["path"] for e in resp.json().get("tree", []) if e.get("type") == "blob")

    def read_file(self, repo: RepoRef, path: str, ref: str) -> FileBlob | None:
        resp = self._req("GET", f"{self._repo(repo)}/contents/{path}", params={"ref": ref}, ok=(200, 404))
        if resp.status_code == 404:
            return None
        data = resp.json()
        if isinstance(data, list) or data.get("type") != "file":
            return None
        if data.get("encoding") == "base64":
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        else:  # large files: fall back to the blob API
            blob = self._req("GET", f"{self._repo(repo)}/git/blobs/{data['sha']}").json()
            content = base64.b64decode(blob["content"]).decode("utf-8", errors="replace")
        return FileBlob(content=content, sha=data["sha"])

    def file_history(self, repo: RepoRef, path: str, ref: str, limit: int = 5) -> list[CommitInfo]:
        resp = self._req("GET", f"{self._repo(repo)}/commits", params={"path": path, "sha": ref, "per_page": limit})
        return [
            CommitInfo(
                sha=c["sha"],
                author_name=c["commit"]["author"]["name"],
                author_email=c["commit"]["author"]["email"],
                message=c["commit"]["message"],
                date=c["commit"]["author"]["date"],
            )
            for c in resp.json()
        ]

    def changed_files(self, repo: RepoRef, base_sha: str, head_sha: str) -> list[str]:
        if base_sha == head_sha:
            return []
        resp = self._req("GET", f"{self._repo(repo)}/compare/{base_sha}...{head_sha}")
        return sorted(
            {f["filename"] for f in resp.json().get("files", [])}
            | {f["previous_filename"] for f in resp.json().get("files", []) if f.get("previous_filename")}
        )

    def commit_message(self, repo: RepoRef, sha: str) -> str:
        return str(self._req("GET", f"{self._repo(repo)}/git/commits/{sha}").json()["message"])

    # -- writes -----------------------------------------------------------
    def _author(self) -> dict[str, str]:
        return {
            "name": self._settings.harness_commit_author_name,
            "email": self._settings.harness_commit_author_email,
            "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _update_ref(self, repo: RepoRef, branch: str, sha: str, *, create: bool, force: bool = False) -> None:
        if force and not branch.startswith(self._settings.harness_branch_prefix):
            raise GitError("forbidden_force_push", "Force updates are only allowed on harness-owned job branches.")
        if create:
            resp = self._req(
                "POST", f"{self._repo(repo)}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}, ok=(201, 422)
            )
            if resp.status_code == 201:
                return
        resp = self._req("PATCH", f"{self._repo(repo)}/git/refs/heads/{branch}", json={"sha": sha, "force": force}, ok=(200, 422))
        if resp.status_code == 422:
            raise StaleRefError(f"{repo.full_name}:{branch} moved; refusing non-fast-forward update.")

    def commit_files(
        self,
        repo: RepoRef,
        branch: str,
        parent_sha: str,
        writes: list[FileWrite],
        message: str,
        *,
        create_branch: bool,
        reset_job_branch: bool = False,
    ) -> str:
        parent = self._req("GET", f"{self._repo(repo)}/git/commits/{parent_sha}").json()
        tree_items: list[dict[str, Any]] = []
        for w in writes:
            if w.content is None:
                tree_items.append({"path": w.path, "mode": "100644", "type": "blob", "sha": None})
            else:
                blob = self._req("POST", f"{self._repo(repo)}/git/blobs", json={"content": w.content, "encoding": "utf-8"}).json()
                tree_items.append({"path": w.path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self._req(
            "POST", f"{self._repo(repo)}/git/trees", json={"base_tree": parent["tree"]["sha"], "tree": tree_items}
        ).json()
        commit = self._req(
            "POST",
            f"{self._repo(repo)}/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [parent_sha], "author": self._author()},
        ).json()
        self._update_ref(repo, branch, commit["sha"], create=create_branch, force=reset_job_branch)
        return str(commit["sha"])

    def restore_tree(self, repo: RepoRef, branch: str, parent_sha: str, tree_from_sha: str, message: str) -> str:
        source = self._req("GET", f"{self._repo(repo)}/git/commits/{tree_from_sha}").json()
        commit = self._req(
            "POST",
            f"{self._repo(repo)}/git/commits",
            json={"message": message, "tree": source["tree"]["sha"], "parents": [parent_sha], "author": self._author()},
        ).json()
        self._update_ref(repo, branch, commit["sha"], create=False)
        return str(commit["sha"])

    def open_pull_request(self, repo: RepoRef, head: str, base: str, title: str, body: str) -> int:
        existing = self._req(
            "GET", f"{self._repo(repo)}/pulls", params={"head": f"{repo.owner}:{head}", "base": base, "state": "open"}
        ).json()
        if existing:
            return int(existing[0]["number"])
        pr = self._req(
            "POST", f"{self._repo(repo)}/pulls", json={"title": title, "head": head, "base": base, "body": body}
        ).json()
        return int(pr["number"])

    def merge_pull_request(self, repo: RepoRef, number: int, expected_head_sha: str, title: str) -> str:
        resp = self._req(
            "PUT",
            f"{self._repo(repo)}/pulls/{number}/merge",
            ok=(200, 405, 409),
            json={"sha": expected_head_sha, "merge_method": "squash", "commit_title": title},
        )
        if resp.status_code == 409:
            raise StaleRefError(f"PR #{number} head changed before merge.")
        if resp.status_code == 405:
            raise GitError("merge_conflict", f"PR #{number} is not mergeable: {resp.json().get('message')}")
        return str(resp.json()["sha"])

    def check_runs(self, repo: RepoRef, sha: str) -> list[CheckRun]:
        runs = self._req("GET", f"{self._repo(repo)}/commits/{sha}/check-runs", params={"per_page": 100}).json()
        out = [
            CheckRun(
                name=r["name"],
                status=r["status"],
                conclusion=r.get("conclusion"),
                url=r.get("html_url"),
                summary=(r.get("output") or {}).get("summary"),
            )
            for r in runs.get("check_runs", [])
        ]
        statuses = self._req("GET", f"{self._repo(repo)}/commits/{sha}/status").json()
        state_map = {"success": "success", "failure": "failure", "error": "failure"}
        for s in statuses.get("statuses", []):
            done = s["state"] in state_map
            out.append(
                CheckRun(
                    name=s["context"],
                    status="completed" if done else "in_progress",
                    conclusion=state_map.get(s["state"]),
                    url=s.get("target_url"),
                    summary=s.get("description"),
                )
            )
        return out

    def create_repository(self, repo: RepoRef, description: str, private: bool) -> RepoInfo:
        """Create a repository in the organisation with an initial commit (the Git Data API needs one)."""
        resp = self._req(
            "POST",
            f"/orgs/{repo.owner}/repos",
            ok=(201, 401, 403, 404, 422),
            json={
                "name": repo.name,
                "description": description[:300],
                "private": private,
                "auto_init": True,
                "has_wiki": False,
                "has_projects": False,
            },
        )
        if resp.status_code in (401, 403, 404):
            # Retryable: fixing the token or the organisation settings makes a retry succeed.
            raise GitError(
                "github_permission_denied",
                f"The GitHub token is not allowed to create repositories in {repo.owner} "
                f"(GitHub said: {resp.json().get('message', resp.status_code) if resp.content else resp.status_code}).",
                retryable=True,
            )
        if resp.status_code == 422:
            raise GitError("repo_exists", f"Repository {repo.full_name} already exists or the name is invalid.")
        data = resp.json()
        return RepoInfo(owner=data["owner"]["login"], name=data["name"], default_branch=data.get("default_branch") or "main")

    def contains_commit(self, repo: RepoRef, branch: str, sha: str) -> bool:
        resp = self._req("GET", f"{self._repo(repo)}/compare/{sha}...{branch}", ok=(200, 404))
        return resp.status_code == 200 and resp.json().get("status") in ("ahead", "identical")


# --------------------------------------------------------------------------
# In-memory provider (tests and local development; no network)
# --------------------------------------------------------------------------


@dataclass
class _Commit:
    sha: str
    parent: str | None
    files: dict[str, str]
    message: str
    author: str


@dataclass
class _Repo:
    info: RepoInfo
    commits: dict[str, _Commit] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)
    pulls: dict[int, dict[str, Any]] = field(default_factory=dict)
    checks: dict[str, list[CheckRun]] = field(default_factory=dict)


class InMemoryGitProvider:
    """A faithful-enough model of a GitHub repository for tests: commits, branches, PRs, CAS refs, checks."""

    HARNESS_AUTHOR = "harness@webblock.invalid"

    def __init__(self) -> None:
        self.repos: dict[str, _Repo] = {}
        self._counter = itertools.count(1)
        # name -> conclusion used for check runs on every new commit on non-default branches
        self.default_check_conclusions: dict[str, str] = {}
        self.write_count = 0

    # -- test helpers ------------------------------------------------------
    def _sha(self, seed: str) -> str:
        return hashlib.sha1(f"{seed}:{next(self._counter)}".encode()).hexdigest()  # noqa: S324

    def create_repo(self, owner: str, name: str, files: dict[str, str], default_branch: str = "main") -> str:
        repo = _Repo(info=RepoInfo(owner=owner, name=name, default_branch=default_branch))
        sha = self._sha("init")
        repo.commits[sha] = _Commit(sha, None, dict(files), "Initial commit", "dev@client.example")
        repo.branches[default_branch] = sha
        self.repos[f"{owner}/{name}"] = repo
        return sha

    def push_user_commit(
        self, owner: str, name: str, branch: str, writes: dict[str, str | None], message: str = "manual edit"
    ) -> str:
        repo = self.repos[f"{owner}/{name}"]
        parent = repo.branches[branch]
        files = dict(repo.commits[parent].files)
        for p, c in writes.items():
            if c is None:
                files.pop(p, None)
            else:
                files[p] = c
        sha = self._sha(message)
        repo.commits[sha] = _Commit(sha, parent, files, message, "dev@client.example")
        repo.branches[branch] = sha
        return sha

    def set_checks(self, owner: str, name: str, sha: str, checks: list[CheckRun]) -> None:
        self.repos[f"{owner}/{name}"].checks[sha] = checks

    def _get(self, repo: RepoRef) -> _Repo:
        r = self.repos.get(repo.full_name)
        if r is None:
            raise GitError("github_404", f"Repository {repo.full_name} not found.")
        return r

    # -- protocol ----------------------------------------------------------
    def get_repo(self, repo: RepoRef) -> RepoInfo | None:
        r = self.repos.get(repo.full_name)
        return r.info if r else None

    def get_branch_head(self, repo: RepoRef, branch: str) -> str | None:
        return self._get(repo).branches.get(branch)

    def _resolve(self, r: _Repo, ref: str) -> _Commit:
        sha = r.branches.get(ref, ref)
        if sha not in r.commits:
            raise GitError("github_404", f"Ref {ref} not found.")
        return r.commits[sha]

    def list_files(self, repo: RepoRef, ref: str) -> list[str]:
        return sorted(self._resolve(self._get(repo), ref).files)

    def read_file(self, repo: RepoRef, path: str, ref: str) -> FileBlob | None:
        files = self._resolve(self._get(repo), ref).files
        return FileBlob(content=files[path], sha=blob_sha(files[path])) if path in files else None

    def file_history(self, repo: RepoRef, path: str, ref: str, limit: int = 5) -> list[CommitInfo]:
        r = self._get(repo)
        out: list[CommitInfo] = []
        commit: _Commit | None = self._resolve(r, ref)
        while commit is not None and len(out) < limit:
            parent = r.commits.get(commit.parent) if commit.parent else None
            before = parent.files.get(path) if parent else None
            if commit.files.get(path) != before:
                out.append(CommitInfo(commit.sha, commit.author, commit.author, commit.message, ""))
            commit = parent
        return out

    def changed_files(self, repo: RepoRef, base_sha: str, head_sha: str) -> list[str]:
        r = self._get(repo)
        a, b = r.commits[base_sha].files, r.commits[head_sha].files
        return sorted(p for p in set(a) | set(b) if a.get(p) != b.get(p))

    def commit_message(self, repo: RepoRef, sha: str) -> str:
        return self._get(repo).commits[sha].message

    def _cas(self, r: _Repo, branch: str, expected_parent: str, new_sha: str, create: bool) -> None:
        current = r.branches.get(branch)
        if current is None and create:
            r.branches[branch] = new_sha
            return
        if current != expected_parent:
            raise StaleRefError(f"{r.info.owner}/{r.info.name}:{branch} moved; refusing non-fast-forward update.")
        r.branches[branch] = new_sha

    def commit_files(
        self,
        repo: RepoRef,
        branch: str,
        parent_sha: str,
        writes: list[FileWrite],
        message: str,
        *,
        create_branch: bool,
        reset_job_branch: bool = False,
    ) -> str:
        r = self._get(repo)
        parent = r.commits[parent_sha]
        files = dict(parent.files)
        for w in writes:
            if w.content is None:
                files.pop(w.path, None)
            else:
                files[w.path] = w.content
        sha = self._sha(message)
        r.commits[sha] = _Commit(sha, parent_sha, files, message, self.HARNESS_AUTHOR)
        if reset_job_branch:
            if not branch.startswith("webblock/"):
                raise GitError("forbidden_force_push", "Force updates are only allowed on harness-owned job branches.")
            r.branches[branch] = sha
        else:
            self._cas(r, branch, parent_sha, sha, create_branch)
        self.write_count += 1
        if branch != r.info.default_branch and self.default_check_conclusions:
            r.checks[sha] = [CheckRun(n, "completed", c) for n, c in self.default_check_conclusions.items()]
        return sha

    def restore_tree(self, repo: RepoRef, branch: str, parent_sha: str, tree_from_sha: str, message: str) -> str:
        r = self._get(repo)
        sha = self._sha(message)
        r.commits[sha] = _Commit(sha, parent_sha, dict(r.commits[tree_from_sha].files), message, self.HARNESS_AUTHOR)
        self._cas(r, branch, parent_sha, sha, False)
        self.write_count += 1
        return sha

    def open_pull_request(self, repo: RepoRef, head: str, base: str, title: str, body: str) -> int:
        r = self._get(repo)
        for number, pr in r.pulls.items():
            if pr["head"] == head and pr["state"] == "open":
                return number
        number = len(r.pulls) + 1
        r.pulls[number] = {"head": head, "base": base, "title": title, "body": body, "state": "open"}
        return number

    def merge_pull_request(self, repo: RepoRef, number: int, expected_head_sha: str, title: str) -> str:
        r = self._get(repo)
        pr = r.pulls[number]
        head_sha = r.branches[pr["head"]]
        if head_sha != expected_head_sha:
            raise StaleRefError(f"PR #{number} head changed before merge.")
        base_sha = r.branches[pr["base"]]
        # Squash: apply the PR's net change onto the base; conflict if both sides touched a file differently.
        merge_base = head_sha
        ancestors = []
        c: _Commit | None = r.commits[head_sha]
        while c is not None:
            ancestors.append(c.sha)
            c = r.commits.get(c.parent) if c.parent else None
        b: _Commit | None = r.commits[base_sha]
        while b is not None and b.sha not in ancestors:
            b = r.commits.get(b.parent) if b.parent else None
        merge_base = b.sha if b else ancestors[-1]
        mb, head, base = r.commits[merge_base].files, r.commits[head_sha].files, r.commits[base_sha].files
        files = dict(base)
        for p in set(mb) | set(head):
            if mb.get(p) != head.get(p):
                if base.get(p) != mb.get(p) and base.get(p) != head.get(p):
                    raise GitError("merge_conflict", f"PR #{number} conflicts on {p}.")
                if head.get(p) is None:
                    files.pop(p, None)
                else:
                    files[p] = head[p]
        sha = self._sha(title)
        r.commits[sha] = _Commit(sha, base_sha, files, title, self.HARNESS_AUTHOR)
        r.branches[pr["base"]] = sha
        pr["state"] = "merged"
        self.write_count += 1
        return sha

    def check_runs(self, repo: RepoRef, sha: str) -> list[CheckRun]:
        return list(self._get(repo).checks.get(sha, []))

    def create_repository(self, repo: RepoRef, description: str, private: bool) -> RepoInfo:
        if repo.full_name in self.repos:
            raise GitError("repo_exists", f"Repository {repo.full_name} already exists or the name is invalid.")
        self.create_repo(repo.owner, repo.name, {"README.md": f"# {repo.name}\n\n{description}\n"})
        self.write_count += 1
        return self.repos[repo.full_name].info

    def contains_commit(self, repo: RepoRef, branch: str, sha: str) -> bool:
        r = self._get(repo)
        c: _Commit | None = r.commits.get(r.branches.get(branch, ""))
        while c is not None:
            if c.sha == sha:
                return True
            c = r.commits.get(c.parent) if c.parent else None
        return False


def make_git_provider(settings: Settings) -> GitProvider:
    if settings.git_provider.value == "memory":
        return InMemoryGitProvider()
    return GitHubProvider(settings)
