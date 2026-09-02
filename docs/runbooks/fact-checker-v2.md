# Fact Checker V2 operations runbook

## Purpose

Fact Checker V2 permits rendering and publication only after evidence-bound claims pass both the deterministic verifier and the semantic critic. A successful run emits a strict `FactCheckReport` with schema version `2.0` and the unique IDs of every confirmed claim.

The safety gate cannot be disabled. `fact_check=False` is retained only for call compatibility and does not bypass verification.

## Required input

New content requires `SourceLineage` schema version `2.0` with a non-empty article ID, source URL, content hash, and evidence passages. Production queue callers must enter through Queue Lineage V2 using `NEWS_COLLECTOR` or reviewed `MANUAL_VERIFIED` metadata. Legacy, synthetic, empty-evidence, and mismatched evidence records are not publishable.

Every generated claim must have:

- a unique `claim_id`;
- a supported claim type;
- one or more known `evidence_ids`;
- normalized number metadata for numerical claims;
- source-bound entities, numbers, and dates;
- at most one CTA, placed last.

## Verification sequence

1. `ClaimGenerator` receives only the attested evidence passages and parses a bounded JSON response.
2. `DeterministicVerifier` rejects unsupported entity, number, date, source, CTA, and evidence references.
3. `SemanticCritic` receives only the evidence selected by each claim and must return a strict supported verdict.
4. `ScriptAssembler` copies verified claim text into content slides without an LLM rewrite.
5. `ContentCreator` emits a `FactCheckReport` whose counts and claim IDs are internally consistent.
6. `validate_publish_quality` checks the report after generation and again immediately before the publisher call.

Any exception, timeout, empty response, malformed JSON, schema mismatch, contradicted verdict, insufficient evidence, or missing report fails closed. The renderer and publisher must receive zero calls when the first quality gate fails; the publisher must receive zero calls when the final gate fails.

## Disabled legacy paths

The following adapters cannot prove SourceLineage V2 and intentionally raise a quality-gate error:

- `fact_checker.check_script`
- `fact_checker.extract_claims`
- `fact_checker.verify_claim`
- `fact_checker._check_hallucination`
- `content_quality_gate.validate_deterministic`
- `content_quality_gate.run_critic`
- `content_quality_gate.validate_content_quality`
- module-level `content_creator.run`

Do not restore permissive return values or mock these missing contracts with `create=True` in production-path tests.

## Failure handling

Treat these representative codes as publication blockers:

- `LEGACY_LINEAGE_UNVERIFIED`, `CLAIM_EVIDENCE_MISSING`, `EVIDENCE_ID_UNKNOWN`
- `CLAIMS_EMPTY`, `CLAIM_ID_DUPLICATE`, `CLAIM_SCHEMA_INVALID`
- `ENTITY_UNSUPPORTED`, `NUMBER_UNSUPPORTED`, `DATE_UNSUPPORTED`
- `CRITIC_PARSE_ERROR`, `CRITIC_RESPONSE_MISMATCH`
- `CLAIM_CONTRADICTED`, `CLAIM_INSUFFICIENT_EVIDENCE`
- `FACT_CHECK_REPORT_INVALID`, `VERIFIED_CLAIMS_MISSING`

Do not retry a malformed or unsupported claim automatically as if it were a transient publisher failure. Correct or recollect the evidence, then create a new verified queue item. Never edit a `FactCheckReport` or its confirmed claim IDs by hand.

## Safe verification

Run from the repository root in an isolated test environment. These tests inject local runnable stubs and must not call live LLM, search, Instagram, or Meta APIs.

PowerShell:

```powershell
$env:ALGO_ENV = "test"
python -m pytest tests/test_claim_generator.py tests/test_quality_gate.py tests/test_content_creator_quality.py tests/test_fact_checker_guards.py -q
python -m pytest tests/test_publish_pipeline.py tests/test_m6_lineage.py tests/test_quality_gate_regression.py -q
```

Expected behavior:

- all listed tests pass;
- live network calls are zero;
- publisher calls are zero on every quality-gate failure;
- no repository or production SQLite database, WAL, or SHM file is created or modified.

## Recovery and rollback

This feature has no database migration. Rollback is a Git deployment rollback to the last reviewed Fact Checker V2 commit while Queue V2 publication remains disabled. Do not re-enable a legacy fact-check adapter to restore service. Preserve the failing lineage, generated claims, error code, and logs for review without copying credentials or raw environment values into the incident record.
