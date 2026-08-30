from __future__ import annotations

from app.reliability.models import FailureTaxonomy, RetryDecision, RetryPolicy


POLICIES: dict[FailureTaxonomy, RetryPolicy] = {
    FailureTaxonomy.transient: RetryPolicy(
        category=FailureTaxonomy.transient,
        max_attempts=3,
        backoff_seconds=(0.05, 0.15),
        action="retry",
    ),
    FailureTaxonomy.rate_limit: RetryPolicy(
        category=FailureTaxonomy.rate_limit,
        max_attempts=3,
        backoff_seconds=(0.1, 0.3),
        respect_retry_after=True,
        action="retry",
    ),
    FailureTaxonomy.schema_invalid: RetryPolicy(
        category=FailureTaxonomy.schema_invalid,
        max_attempts=3,
        backoff_seconds=(0, 0),
        action="repair_then_retry",
    ),
    FailureTaxonomy.business_rule: RetryPolicy(
        category=FailureTaxonomy.business_rule, max_attempts=1, action="fail"
    ),
    FailureTaxonomy.permission_denied: RetryPolicy(
        category=FailureTaxonomy.permission_denied, max_attempts=1, action="fail"
    ),
    FailureTaxonomy.concurrency_conflict: RetryPolicy(
        category=FailureTaxonomy.concurrency_conflict,
        max_attempts=3,
        backoff_seconds=(0.05, 0.15),
        action="reread_then_retry",
    ),
    FailureTaxonomy.permanent: RetryPolicy(
        category=FailureTaxonomy.permanent, max_attempts=1, action="fail"
    ),
    FailureTaxonomy.unknown: RetryPolicy(
        category=FailureTaxonomy.unknown,
        max_attempts=2,
        backoff_seconds=(0.05,),
        action="retry",
    ),
}


def retry_decision(
    *,
    component: str,
    category: FailureTaxonomy,
    signature: str,
    attempt: int,
    budget_remaining: int,
    retry_after_seconds: float | None = None,
) -> RetryDecision:
    policy = POLICIES[category]
    allowed = (
        policy.action not in {"fail", "quarantine"}
        and attempt < policy.max_attempts
        and budget_remaining > 0
    )
    delay = 0.0
    if allowed:
        if policy.respect_retry_after and retry_after_seconds is not None:
            delay = max(0.0, retry_after_seconds)
        elif policy.backoff_seconds:
            delay = policy.backoff_seconds[min(attempt - 1, len(policy.backoff_seconds) - 1)]
    reason = "policy_allows_retry"
    if budget_remaining <= 0:
        reason = "task_retry_budget_exhausted"
    elif policy.action in {"fail", "quarantine"}:
        reason = f"category_action_{policy.action}"
    elif attempt >= policy.max_attempts:
        reason = "component_attempt_limit_reached"
    return RetryDecision(
        component=component,
        category=category,
        error_signature=signature,
        attempt=attempt,
        allowed=allowed,
        delay_seconds=delay,
        reason=reason,
    )
