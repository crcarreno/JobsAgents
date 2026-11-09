"""Read-only OpenTask smoke test.

Usage:
    python -m scripts.test_opentask

Reads OPENTASK_API_TOKEN from the environment or .env, checks the authenticated
profile, verifies capabilities:read, lists the public catalog and prints a
summary of up to five tasks plus the detail of the first one.

This script never writes to MySQL and never mutates OpenTask.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(os.getenv("JOBAGENTS_ENV_FILE", PROJECT_ROOT / ".env"))

from core.config import Config
from core.exceptions import PlatformAuthError, PlatformError, PlatformRateLimitError
from app.adapters.opentask_adapter import OpenTaskAdapter
from app.services.settings_service import SettingsService


def _platform_config(config: Config) -> dict:
    for item in config.get("bootstrap.platforms", []) or []:
        if item.get("slug") == "opentask":
            return item.get("config", {}) or {}
    return {}


def _safe_message(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text[:400].replace("\n", " ")


def main() -> int:
    config = Config.load()
    settings = SettingsService(config)
    token = settings.get_opentask_token()
    if not token:
        print("OPENTASK_API_TOKEN is not configured.")
        print("Add it to .env or export it in the environment. It is never printed by this script.")
        return 2

    platform = SimpleNamespace(
        slug="opentask",
        automation_permission="unknown",
        config=_platform_config(config),
    )
    adapter = OpenTaskAdapter(platform, config, settings, token=token)

    print("== OpenTask connection ==")
    connection = adapter.connect()
    print(f"status: {connection.status}")
    print(f"message: {connection.message}")
    if not connection.ok:
        return 1
    details = connection.details or {}
    print(f"profile_id: {details.get('profile_id')}")
    print(f"handle: {details.get('handle')}")
    print(f"display_name: {details.get('display_name')}")
    print(f"kind: {details.get('kind')}")
    print(f"service_listing_status: {details.get('service_listing_status')}")
    stats = details.get("stats") or {}
    print(
        "stats: "
        f"tasksPosted={stats.get('tasksPosted')} activeBids={stats.get('activeBids')} "
        f"contractsAsSeller={stats.get('contractsAsSeller')}"
    )

    print("\n== Scopes ==")
    print("profile:read: OK")
    try:
        capabilities = adapter.list_capabilities(limit=1)
        print(f"capabilities:read: OK ({len(capabilities)} capability record(s) returned)")
    except PlatformAuthError as exc:
        print(f"capabilities:read: DENIED/insufficient ({_safe_message(exc)})")
    except PlatformError as exc:
        print(f"capabilities:read: ERROR ({_safe_message(exc)})")

    print("\n== Catalog ==")
    try:
        candidates = adapter.search_tasks()
    except PlatformRateLimitError as exc:
        print(f"rate limited: {_safe_message(exc)}")
        return 1
    except PlatformError as exc:
        print(f"catalog error: {_safe_message(exc)}")
        return 1

    print(f"tasks_found: {len(candidates)}")
    for index, candidate in enumerate(candidates[:5], start=1):
        raw_task = (candidate.raw or {}).get("task") or candidate.raw or {}
        print(
            f"{index}. {candidate.external_id} | {candidate.title[:90]} | "
            f"{candidate.reward:g} {candidate.currency} | "
            f"{raw_task.get('executionMode') or '-'} | "
            f"deadline={raw_task.get('deadline') or '-'} | {candidate.url}"
        )

    if candidates:
        first = candidates[0]
        print("\n== Detail of first task ==")
        detail = None
        try:
            detail = adapter.get_task_details(first)
        except PlatformAuthError as exc:
            print(f"authenticated detail failed: {_safe_message(exc)}")
            print("retrying through the public detail endpoint (read-only)...")
            public_adapter = OpenTaskAdapter(platform, config, settings, token="")
            try:
                detail = public_adapter.get_task_details(first)
            except PlatformError as public_exc:
                print(f"public detail failed: {_safe_message(public_exc)}")
        except PlatformError as exc:
            print(f"detail error: {_safe_message(exc)}")

        if detail is not None:
            extra = detail.extra or {}
            print(f"id: {detail.external_id}")
            print(f"title: {detail.title}")
            print(f"reward: {detail.reward:g} {detail.currency}")
            print(f"execution_mode: {extra.get('execution_mode')}")
            print(f"delivery_structure: {extra.get('delivery_structure')}")
            print(f"status: {extra.get('status')}")
            funding = extra.get("funding_status") or "not reported"
            print(f"funding_status: {funding} (declared reward is not proof of escrow)")
            print(f"deadline: {extra.get('deadline') or (detail.deadline.isoformat() if detail.deadline else None)}")
            print(f"expected_output: {detail.expected_output}")
            acceptance = extra.get("acceptance_criteria")
            if acceptance:
                print(f"acceptance_criteria: {str(acceptance)[:400]}")
            print(f"description: {detail.description[:400]}")

    print("\nOpenTask read-only test completed. No records were written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
