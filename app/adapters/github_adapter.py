import base64
import hashlib
import re
import time
from datetime import datetime
from typing import Any

import httpx

from core.exceptions import (
    AutomationNotAllowedError,
    PlatformAuthError,
    PlatformError,
    PlatformRateLimitError,
)
from core.logger import get_logger
from app.adapters.base_adapter import (
    BaseAdapter,
    ConnectionResult,
    SubmissionResult,
    TaskCandidate,
    TaskDetail,
)

AMOUNT_PATTERN = re.compile(r"(?:US\$|USD|\$)\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE)
DENY_AUTOMATION_PATTERN = re.compile(
    r"(?i)(no\s+(ai|bots?|automation|automated)|automation\s+(is\s+)?not\s+(allowed|permitted)|"
    r"bots?\s+(are\s+)?not\s+(allowed|permitted)|do\s+not\s+use\s+(ai|bots?|automation))"
)


class GitHubBountyAdapter(BaseAdapter):
    adapter_name = "github_bounty"
    platform_documentation = {
        "platform": "GitHub Issues with bounty labels",
        "task_types": "Open source software contributions, bug fixes and feature requests funded by bounties",
        "countries": "GitHub accounts are available worldwide; payout eligibility depends on the bounty funder",
        "registration": "GitHub account plus a funded bounty program account (for example Algora or a direct sponsor)",
        "authentication": "GitHub fine-grained or classic token with public_repo scope (repo scope for private repositories)",
        "apis": "Official GitHub REST API v3 (https://docs.github.com/rest) and GitHub search API",
        "automation_restrictions": (
            "GitHub permits API automation. Each repository and bounty funder may impose additional rules, so the "
            "system requires an explicit per-platform review and refuses to submit to repositories whose issues deny "
            "automation. Automated PRs must disclose that they were prepared by an agent."
        ),
        "payments": (
            "Payouts are handled outside GitHub by the bounty funder. The adapter cannot verify payment status; "
            "earnings must be confirmed manually in the panel."
        ),
        "fees": "Depends on the bounty funder (for example Algora contributors keep 100% of the bounty)",
        "limits": "GitHub search API: 30 authenticated requests/minute; core API: 5000 requests/hour",
    }

    def __init__(self, platform, config, settings, token: str = ""):
        super().__init__(platform, config, settings, token)
        self.logger = get_logger("adapter.github")
        self.api_base = str(self.option("api_base", "https://api.github.com")).rstrip("/")
        self.max_results = int(self.option("max_results", 30))
        self.min_reward = float(self.option("min_reward_usd", 0.5))
        self.claim_comment = str(self.option("claim_comment", "") or "").strip()
        self.authenticated_login: str | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": str(self.settings.get("ai.user_agent", "jobagents-agent/1.0")),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.api_base}{path}"
        try:
            with httpx.Client(timeout=httpx.Timeout(45)) as client:
                response = client.request(method, url, headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            raise PlatformError(f"GitHub request failed: {exc}") from exc
        if response.status_code == 401:
            raise PlatformAuthError("GitHub token is invalid or expired")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            reset = response.headers.get("x-ratelimit-reset", "unknown")
            raise PlatformRateLimitError(f"GitHub rate limit exceeded; resets at {reset}")
        if response.status_code == 404:
            raise PlatformError(f"GitHub resource not found: {path}")
        if response.status_code >= 400:
            raise PlatformError(f"GitHub API error {response.status_code}: {response.text[:400]}")
        return response

    def connect(self) -> ConnectionResult:
        if not self.token:
            self._connection = ConnectionResult(
                ok=False,
                status="not_configured",
                message="GitHub token is not configured; discovery works read-only but claiming and delivery do not",
            )
            return self._connection
        try:
            response = self._request("GET", "/user")
            payload = response.json()
            self.authenticated_login = payload.get("login")
            self._connection = ConnectionResult(
                ok=True,
                status="ok",
                message=f"Connected as {self.authenticated_login}",
                details={"login": self.authenticated_login, "scopes": response.headers.get("x-oauth-scopes", "")},
            )
        except PlatformError as exc:
            self._connection = ConnectionResult(ok=False, status="error", message=str(exc))
        return self._connection

    def _extract_reward(self, text: str) -> float:
        best = 0.0
        for match in AMOUNT_PATTERN.finditer(text or ""):
            raw = match.group(1).replace(",", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            best = max(best, value)
        return best

    def _parse_issue(self, item: dict[str, Any]) -> TaskCandidate | None:
        if item.get("pull_request"):
            return None
        repo_url = item.get("repository_url", "")
        repo = repo_url.rsplit("/repos/", 1)[-1] if "/repos/" in repo_url else ""
        if not repo and item.get("html_url"):
            parts = item["html_url"].split("/")
            if len(parts) >= 5:
                repo = f"{parts[3]}/{parts[4]}"
        allowed = self.option("allowed_repos", []) or []
        if allowed and repo.lower() not in {str(r).lower() for r in allowed}:
            return None
        labels = [label.get("name", "") for label in item.get("labels", [])]
        body = item.get("body") or ""
        reward_text = " ".join(labels) + " " + (item.get("title") or "") + " " + body[:2000]
        reward = self._extract_reward(reward_text)
        if reward < self.min_reward:
            return None
        number = item.get("number")
        return TaskCandidate(
            external_id=f"{repo}#{number}",
            title=(item.get("title") or "")[:500],
            url=item.get("html_url") or "",
            description=body,
            reward=reward,
            currency="USD",
            created_at=self._parse_dt(item.get("created_at")),
            raw={
                "repo": repo,
                "number": number,
                "labels": labels,
                "state": item.get("state"),
                "author": (item.get("user") or {}).get("login"),
                "comments": item.get("comments"),
                "updated_at": item.get("updated_at"),
                "created_at": item.get("created_at"),
                "assignees": [a.get("login") for a in item.get("assignees", [])],
            },
        )

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    def search_tasks(self, since: datetime | None = None) -> list[TaskCandidate]:
        connection = self._connection or self.connect()
        if not connection.ok and connection.status == "error":
            raise PlatformError(f"Cannot search GitHub: {connection.message}")
        queries = self.option("search_queries", []) or []
        results: dict[str, TaskCandidate] = {}
        per_query = max(1, self.max_results // max(1, len(queries)))
        for query in queries:
            params = {
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": min(100, per_query),
            }
            response = self._request("GET", "/search/issues", params=params)
            payload = response.json()
            for item in payload.get("items", []):
                candidate = self._parse_issue(item)
                if candidate is None:
                    continue
                if since and candidate.created_at and candidate.created_at < since:
                    continue
                results.setdefault(candidate.external_id, candidate)
            if len(results) >= self.max_results:
                break
            time.sleep(1.2)
        return list(results.values())[: self.max_results]

    def _repo_parts(self, candidate: TaskCandidate) -> tuple[str, str]:
        repo = candidate.raw.get("repo") or ""
        if "/" not in repo:
            raise PlatformError(f"Invalid repository for task {candidate.external_id}")
        owner, name = repo.split("/", 1)
        return owner, name

    def get_task_details(self, candidate: TaskCandidate) -> TaskDetail:
        owner, name = self._repo_parts(candidate)
        number = candidate.raw.get("number")
        response = self._request("GET", f"/repos/{owner}/{name}/issues/{number}")
        issue = response.json()
        if issue.get("pull_request"):
            raise PlatformError("Task is a pull request, not an issue")
        if issue.get("state") != "open":
            raise PlatformError("Task is no longer open")
        comments: list[dict[str, Any]] = []
        try:
            comments_response = self._request(
                "GET", f"/repos/{owner}/{name}/issues/{number}/comments", params={"per_page": 50}
            )
            comments = comments_response.json()
        except PlatformError:
            comments = []
        repo_info = {}
        try:
            repo_info = self._request("GET", f"/repos/{owner}/{name}").json()
        except PlatformError:
            repo_info = {}
        labels = [label.get("name", "") for label in issue.get("labels", [])]
        body = issue.get("body") or ""
        reward_text = " ".join(labels) + " " + (issue.get("title") or "") + " " + body[:2000]
        reward = self._extract_reward(reward_text)
        description_parts = [body]
        if comments:
            description_parts.append("\n\n--- Maintainer and contributor comments ---\n")
            for comment in comments[-20:]:
                description_parts.append(f"\n[{(comment.get('user') or {}).get('login')}]: {comment.get('body') or ''}")
        languages = repo_info.get("language")
        milestone = issue.get("milestone") or {}
        return TaskDetail(
            external_id=candidate.external_id,
            title=(issue.get("title") or candidate.title)[:500],
            url=issue.get("html_url") or candidate.url,
            description="\n".join(description_parts)[:20000],
            reward=reward if reward > 0 else candidate.reward,
            currency="USD",
            created_at=self._parse_dt(issue.get("created_at")),
            expected_output=(
                "A pull request that resolves the issue, follows the repository contribution guidelines, "
                "and includes tests when the repository has a test suite."
            ),
            language=languages,
            deadline=self._parse_dt(milestone.get("due_on")) if milestone else None,
            extra={
                "repo": f"{owner}/{name}",
                "number": number,
                "labels": labels,
                "comments": [c.get("body", "")[:3000] for c in comments[-10:]],
                "default_branch": repo_info.get("default_branch", "main"),
                "license": (repo_info.get("license") or {}).get("spdx_id"),
                "permissions": repo_info.get("permissions"),
                "topics": repo_info.get("topics", []),
            },
            raw={**candidate.raw, "repo": f"{owner}/{name}"},
        )

    def check_automation_permission(self, candidate: TaskCandidate) -> tuple[str, str]:
        repo = str(candidate.raw.get("repo") or "").lower()
        allowed_repos = {str(r).lower() for r in (self.option("automation_allowed_repos", []) or [])}
        denied_repos = {str(r).lower() for r in (self.option("automation_denied_repos", []) or [])}
        if repo in denied_repos:
            return "denied", f"Repository {repo} is listed as automation-denied in the platform configuration"
        text = f"{candidate.title}\n{candidate.description}"
        if DENY_AUTOMATION_PATTERN.search(text):
            return "denied", "The issue text explicitly denies automated or bot contributions"
        if repo in allowed_repos:
            return "allowed", f"Repository {repo} was manually approved for automated contributions"
        if self.platform.automation_permission == "allowed" and repo in allowed_repos:
            return "allowed", "Platform and repository automation permission recorded by the operator"
        return (
            "unknown",
            "GitHub API automation is permitted, but this repository has not been explicitly approved for "
            "autonomous contributions. Confirm the repository policy and add it to automation_allowed_repos.",
        )

    def claim_task(self, task) -> SubmissionResult | None:
        if not self.token:
            raise PlatformAuthError("GitHub token is required to claim a task")
        if not self.claim_comment:
            raise AutomationNotAllowedError(
                "No claim comment configured. Set claim_comment only when the bounty program documents a claim mechanism."
            )
        owner, name = task_delivery_repo(task)
        number = (task.raw_payload or {}).get("number") or task.external_id.split("#")[-1]
        if not owner:
            raise PlatformError("Cannot determine repository for task claim")
        existing = self._request(
            "GET", f"/repos/{owner}/{name}/issues/{number}/comments", params={"per_page": 100}
        ).json()
        login = self.authenticated_login or (self.connect().details or {}).get("login")
        for comment in existing:
            if (comment.get("user") or {}).get("login") == login and self.claim_comment in (
                comment.get("body") or ""
            ):
                return SubmissionResult(
                    reference=str(comment.get("id")),
                    url=comment.get("html_url") or "",
                    status="already_claimed",
                    message="Claim comment already exists",
                )
        response = self._request(
            "POST",
            f"/repos/{owner}/{name}/issues/{number}/comments",
            json={"body": self.claim_comment},
        )
        payload = response.json()
        return SubmissionResult(
            reference=str(payload.get("id")),
            url=payload.get("html_url") or "",
            status="claimed",
            message="Claim comment posted",
        )

    def submit_task(self, task, artifacts: list[dict[str, Any]], summary: str = "") -> SubmissionResult:
        if not self.token:
            raise PlatformAuthError("GitHub token is required to submit a pull request")
        if not artifacts:
            raise PlatformError("No artifacts to submit")
        owner, name = task_delivery_repo(task)
        if not owner:
            raise PlatformError("Cannot determine target repository for delivery")
        connection = self.connect()
        if not connection.ok:
            raise PlatformAuthError(connection.message)
        login = self.authenticated_login
        raw = task.raw_payload or {}
        number = raw.get("number") or task.external_id.split("#")[-1]
        base_branch = str((task.evaluation or {}).get("default_branch") or raw.get("default_branch") or "main")

        repo_payload = self._request("GET", f"/repos/{owner}/{name}").json()
        base_branch = repo_payload.get("default_branch") or base_branch
        head_owner = owner if (repo_payload.get("permissions") or {}).get("push") else login
        if head_owner != owner:
            self._ensure_fork(owner, name, login)

        branch = f"jobagents/{str(number)}-{hashlib.sha1(task.external_id.encode()).hexdigest()[:8]}"
        head_ref = f"{head_owner}:{branch}"
        existing_pr = self._find_existing_pr(owner, name, head_ref)
        if existing_pr:
            return SubmissionResult(
                reference=str(existing_pr.get("number")),
                url=existing_pr.get("html_url") or "",
                status="already_submitted",
                message="Pull request already exists",
            )

        try:
            base_sha = self._request(
                "GET", f"/repos/{head_owner}/{name}/git/ref/heads/{base_branch}"
            ).json()["object"]["sha"]
        except PlatformError:
            base_sha = self._request(
                "GET", f"/repos/{owner}/{name}/git/ref/heads/{base_branch}"
            ).json()["object"]["sha"]
        base_commit = self._request("GET", f"/repos/{head_owner}/{name}/git/commits/{base_sha}").json()
        base_tree_sha = base_commit["tree"]["sha"]
        tree_entries = []
        for artifact in artifacts:
            path = str(artifact.get("path") or "").lstrip("/")
            content = artifact.get("content")
            if not path or not isinstance(content, str):
                continue
            blob = self._request(
                "POST",
                f"/repos/{head_owner}/{name}/git/blobs",
                json={"content": base64.b64encode(content.encode("utf-8")).decode("ascii"), "encoding": "base64"},
            ).json()
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if not tree_entries:
            raise PlatformError("No valid text artifacts to submit")
        tree = self._request(
            "POST",
            f"/repos/{head_owner}/{name}/git/trees",
            json={"base_tree": base_tree_sha, "tree": tree_entries},
        ).json()
        commit_message = f"Agent contribution for #{number}: {task.title[:120]}"
        commit = self._request(
            "POST",
            f"/repos/{head_owner}/{name}/git/commits",
            json={"message": commit_message, "tree": tree["sha"], "parents": [base_sha]},
        ).json()
        try:
            self._request(
                "POST",
                f"/repos/{head_owner}/{name}/git/refs",
                json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
            )
        except PlatformError:
            self._request(
                "PATCH",
                f"/repos/{head_owner}/{name}/git/refs/heads/{branch}",
                json={"sha": commit["sha"], "force": True},
            )

        body = (
            f"## Summary\n\n{summary or task.title}\n\n"
            f"Closes #{number}\n\n"
            f"---\n\n"
            f"This pull request was prepared by an autonomous agent and reviewed against the task requirements. "
            f"All changes are limited to the repository files listed in the diff."
        )
        labels = [str(label) for label in (self.option("pr_labels", []) or [])]
        pr_payload = {
            "title": f"{task.title[:200]}",
            "head": head_ref,
            "base": base_branch,
            "body": body,
            "draft": bool(self.option("submit_as_draft", True)),
        }
        if labels:
            pr_payload["labels"] = labels
        pr = self._request("POST", f"/repos/{owner}/{name}/pulls", json=pr_payload).json()
        return SubmissionResult(
            reference=str(pr.get("number")),
            url=pr.get("html_url") or "",
            status="submitted",
            message="Pull request created",
        )

    def _ensure_fork(self, owner: str, name: str, login: str | None) -> None:
        if not login:
            raise PlatformAuthError("Cannot determine authenticated GitHub user for forking")
        try:
            self._request("GET", f"/repos/{login}/{name}")
            return
        except PlatformError:
            pass
        self._request("POST", f"/repos/{owner}/{name}/forks", json={"default_branch_only": False})
        wait_seconds = int(self.option("fork_wait_seconds", 30))
        for _ in range(max(1, wait_seconds // 2)):
            time.sleep(2)
            try:
                self._request("GET", f"/repos/{login}/{name}")
                return
            except PlatformError:
                continue
        raise PlatformError("Fork was not ready before the timeout")

    def _find_existing_pr(self, owner: str, name: str, head_ref: str) -> dict | None:
        try:
            pulls = self._request(
                "GET", f"/repos/{owner}/{name}/pulls", params={"head": head_ref, "state": "all", "per_page": 10}
            ).json()
        except PlatformError:
            return None
        return pulls[0] if isinstance(pulls, list) and pulls else None

    def check_task_status(self, task) -> dict[str, Any]:
        owner, name = task_delivery_repo(task)
        if not owner:
            return {"status": "unknown", "message": "Repository is unknown"}
        number = (task.raw_payload or {}).get("number") or task.external_id.split("#")[-1]
        issue = self._request("GET", f"/repos/{owner}/{name}/issues/{number}").json()
        result = {"issue_state": issue.get("state"), "status": "open" if issue.get("state") == "open" else "closed"}
        if task.delivery_reference:
            try:
                pr = self._request("GET", f"/repos/{owner}/{name}/pulls/{task.delivery_reference}").json()
                result.update(
                    {
                        "pr_state": pr.get("state"),
                        "merged": bool(pr.get("merged")),
                        "mergeable_state": pr.get("mergeable_state"),
                        "review_comments": pr.get("review_comments"),
                    }
                )
                if pr.get("merged"):
                    result["status"] = "accepted"
                elif pr.get("state") == "closed":
                    result["status"] = "rejected"
                else:
                    result["status"] = "submitted"
            except PlatformError as exc:
                result["status_message"] = str(exc)[:300]
        return result

    def get_payment_status(self, task) -> dict[str, Any]:
        return {
            "status": "unverifiable",
            "message": (
                "GitHub does not expose bounty payout status. Confirm payment in the bounty program dashboard and "
                "register the earning manually in the earnings panel."
            ),
        }


def task_delivery_repo(task) -> tuple[str, str]:
    repo = (task.raw_payload or {}).get("repo") or ""
    if "/" not in repo:
        repo = (task.evaluation or {}).get("repo") or ""
    if "/" not in repo:
        return "", ""
    owner, name = repo.split("/", 1)
    return owner, name
