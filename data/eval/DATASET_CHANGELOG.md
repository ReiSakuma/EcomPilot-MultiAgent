# Interview Eval Dataset Changelog

## interview-eval-v1

- Frozen 40 cases across normal, business constraint, model failure, browser failure,
  recovery, context/memory, and adversarial categories.
- Frozen a 20-case live LLM subset.
- Every case has a stable id, explicit expectation, failure domain, and rationale.
- Model protocol cases use local fault injection; they are not reported as live model calls.
- Browser control and script cases are regression contracts; Chromium evidence remains a
  separate online stage.

## v54 market-price-gate expectation update

- Kept the frozen 40-case category distribution and case IDs unchanged.
- Moved two normal wireless-earbud prices inside their audience-specific core market bands.
- Reclassified cost/margin versus market conflicts as recoverable `Price Confirmation`
  outcomes that stop before Review and before side effects.
- Updated recovery evidence to expect restart from the market-price gate and its descendants.
