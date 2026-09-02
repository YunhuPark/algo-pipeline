# Instagram Publishing Quality Audit & Fail-Closed Remediation
**Date:** 2026-07-28
**Auditor:** AntiGravity Lead AI Engineer

## 1. Issue: Instagram Automatic Upload False-Success
* **Cause**: `scripts/run_daily.py` relied on `pipeline.run_pipeline` returning a zero exit code (or valid paths) to assume upload success, without verifying if the upload step itself (`ig_post_id` generation) succeeded.
* **Impact**: Jobs generated images and logged them as published even when Meta API rejected the credentials or payload.

## 2. Issue: Quality Gate Evasion
* **Cause**: `validate_content_quality` (or its equivalent) was not rigorously enforced right before the publisher call. `pipeline.py` did not check `fact_disputed` or source inconsistencies to block uploads.
* **Impact**: Unverifiable claims and mismatched sources could be passed to Instagram if the fallback mechanisms failed.

## 3. Remediation Implemented
1. **PipelineResult**: Replaced `list[Path]` with a strongly-typed `PipelineResult` containing explicit `publish_succeeded` and `ig_post_id` fields.
2. **Quality Gate Injection**: Inserted fail-closed checks (`validate_content_quality`) inside `_run_once` immediately before calling `ig_publisher`. Fails if Topic-Source mismatch, `fact_disputed > 0`, or listicle fact-checking bypass is detected.
3. **Preflight Module**: Added `src/api/preflight.py` to classify Meta API token errors (`TOKEN_INVALID`, etc.) locally via mocked checks before execution.
4. **Queue Fix**: `content_queue.py` now explicitly checks `res.publish_succeeded` before marking an item as `published`.

## 4. Verification
* Verified via `test_publish_pipeline.py` that `publisher` exception propagates properly, and empty `ig_post_id` counts as a failure.
* Tests verify Topic-Source mismatch and disputed claims are blocked by `QualityGateError`.
* Database remains unaltered.

## 5. Topic-Source Lineage & Hallucination Fix (2026-08-01)
* **Issue**: `scripts/run_daily.py` independently generated an arbitrary topic via `_pick_topic()` when the queue was empty, causing a disconnect between the stated topic and the actual news source content, resulting in hallucinatory scripts.
* **Remediation**:
    1. Removed `_pick_topic()` logic completely.
    2. Enforced fail-closed behavior if `collect_and_select()` fails.
    3. Introduced `SourceLineage` in `schemas/card_news.py` to firmly bind the canonical `topic`, `source_title`, `source_url`, and `context`.
    4. Updated `src/pipeline.py` to prioritize `source_lineage` and explicitly write the canonical topic to `meta.json` and database entries.
* **Verification**: `tests/test_m6_lineage.py` confirms that the correct lineage propagates to metadata, folder names, and database without live API calls, and that execution fails cleanly on empty data. All 70 offline regression tests passed.
