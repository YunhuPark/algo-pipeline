# Weekly card-news quality review

## Purpose

The weekly review turns generation, quality-gate, human-edit, and mature Instagram performance records into one auditable improvement proposal. Statistics are computed deterministically; no LLM decides the metric values.

## Inputs

- `content_runs` where `origin='real_pipeline'`
- failed `quality_checks`
- `editorial_feedback_events`, including edit ratio, claim corrections, approval, and review duration
- `performance_snapshots` where `is_provisional=0`, linked to a real run through `run_publications`

Synthetic/test runs, unlinked publications, and snapshots younger than 48 hours are excluded. A report requires at least three real runs and one explicit approval/rejection decision. Edit-only events do not satisfy the decision threshold. Otherwise its status is `INSUFFICIENT_DATA` and `experiment_proposal` is null.

## Run manually

Keep publishing disabled while validating the report:

```powershell
$env:ALGO_ENV = "production"
$env:AGENT_AUTO_UPLOAD = "false"
$env:AGENT_DRY_RUN = "true"
python scripts/run_weekly_quality_review.py
```

The same operation is available on the local dashboard at `/quality-review`. Re-running the same Monday-to-Monday window is idempotent.

## Safety contract

The review may insert a `weekly_quality_reviews` row. It must not:

- call Instagram or upload media;
- transition queue items;
- activate or alter a policy;
- activate an experiment;
- include synthetic or provisional data.

A ready review contains exactly one proposal with status `DRAFT` and `requires_human_approval=true`. Turning that proposal into an experiment is a separate reviewed action.

## Scheduling

After manual validation, `python -m src.scheduler` syncs Instagram insights at `WEEKLY_INSIGHTS_HOUR` (08:00 Asia/Seoul by default), then runs the safe review at `WEEKLY_REVIEW_HOUR` (09:00 by default). To run only the review immediately, use `python -m src.scheduler --quality-review`. Scheduling the review does not authorize publication, policy activation, or experiment activation.
