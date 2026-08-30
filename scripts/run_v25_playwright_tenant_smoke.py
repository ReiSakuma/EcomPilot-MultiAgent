from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


BASE_URL = os.getenv("ECOMPILOT_V25_BASE_URL", "http://127.0.0.1:8145").rstrip("/")


def request(
    path: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(call, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def plan(title: str) -> dict:
    return {
        "operation": "update_listing",
        "product_id": "playwright-shared-product",
        "title": title,
        "bullets": ["低延迟", "长续航"],
        "price": 199,
        "stock": 800,
        "coupon": 20,
    }


def main() -> None:
    identities = {
        "tenant_demo": "demo-merchant-a",
        "tenant_beta": "demo-merchant-b",
    }
    results: dict[str, dict] = {}
    snapshots: dict[str, dict] = {}
    verifications: dict[str, dict] = {}
    for tenant_id, token in identities.items():
        request("/seller-center/reset", token=token, method="POST")
        execution = request(
            "/seller-center/execute",
            token=token,
            method="POST",
            payload={
                "plan": plan(f"{tenant_id} 的 Playwright 商品"),
                "idempotency_key": "same-playwright-key",
                "approval": {
                    "approved": True,
                    "approver": token,
                    "reason": "v25 playwright tenant smoke",
                },
            },
        )
        verification = request(
            "/seller-center/verify",
            token=token,
            method="POST",
            payload=plan(f"{tenant_id} 的 Playwright 商品"),
        )
        results[tenant_id] = execution
        verifications[tenant_id] = verification
        snapshots[tenant_id] = request("/seller-center/state", token=token)

    demo_product = snapshots["tenant_demo"]["products"]["playwright-shared-product"]
    beta_product = snapshots["tenant_beta"]["products"]["playwright-shared-product"]
    checks = {
        "both_used_real_playwright": all(
            result.get("backend") == "playwright" for result in results.values()
        ),
        "execution_tenant_matches_identity": all(
            results[tenant]["tenant_id"] == tenant for tenant in identities
        ),
        "same_idempotency_key_did_not_cross_replay": all(
            result["idempotent_replay"] is False for result in results.values()
        ),
        "same_product_id_has_distinct_titles": demo_product["title"]
        != beta_product["title"],
        "verification_used_real_playwright": all(
            result.get("backend") == "playwright" and result.get("verified") is True
            for result in verifications.values()
        ),
        "screenshots_are_tenant_partitioned": all(
            Path(results[tenant]["screenshot_path"]).parent.name == tenant
            and Path(verifications[tenant]["screenshot_path"]).parent.name == tenant
            for tenant in identities
        ),
        "state_api_is_tenant_scoped": all(
            snapshots[tenant]["tenant_id"] == tenant for tenant in identities
        ),
    }
    report = {
        "version": "v25",
        "backend": "playwright",
        "passed": all(checks.values()),
        "checks": checks,
        "execution_screenshots": {
            tenant: result["screenshot_path"] for tenant, result in results.items()
        },
        "verification_screenshots": {
            tenant: result["screenshot_path"]
            for tenant, result in verifications.items()
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
