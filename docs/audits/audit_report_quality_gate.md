# Quality Gate & Pipeline Refactoring Audit

**Date**: 2026-08-03
**Status**: Completed

## 1. Summary of Changes
- Deprecated legacy `fact_checker.py` and `content_creator.py` (which directly piped LLM generation output without strict fact boundaries) and replaced them with the new Claim-based `DeterministicVerifier` and `SemanticCritic` architecture.
- Added strict evidence-bound generation where `CardNewsScript` is synthesized directly from structured `Claim` objects.
- Achieved **100% test coverage** on existing and new validation mechanisms (102 passing tests), validating isolation of test suites and strict fallback behavior.
- Eliminated cross-process DB contention during parallel Pytest execution by refactoring DB factory routing.

## 2. Core Components Updated
1. **Schema Refactoring**:
   - Upgraded `src/schemas/card_news.py` to support `SourceLineage`, `EvidencePassage`, `Claim`, and `SemanticCriticResult` schemas with versioning (`schema_version="2.0"`).
2. **Quality Gate Verification**:
   - Implemented `DeterministicVerifier` (`src/qa/deterministic_verifier.py`) applying hard rules for Unicode Normalization, number constraints, exact-match bounding, and date boundary checks.
   - Implemented `SemanticCritic` (`src/qa/semantic_critic.py`) to interrogate semantic drift via localized language model evaluations.
3. **Generation Separation**:
   - Added `ClaimGenerator` (`src/qa/claim_generator.py`) to systematically extract atomic claims from source texts.
   - Added `ScriptAssembler` (`src/qa/script_assembler.py`) which acts as a deterministic synthesis layer ensuring assembled scripts contain **no new LLM-induced injections**.

## 3. Secret Scan & Compliance
- Conducted codebase-wide reproducible secret scan using `scratch/secret_scan.py` checking for OpenAI keys (`sk-...`), AWS credentials (`AKIA...`), and generic tokens.
- **Result**: No secrets or hardcoded tokens were found in plaintext across the `src/` or `tests/` directories.

## 4. DB Tracking Isolation Check
- Module-level path injections were successfully refactored. Database paths are now strictly evaluated dynamically via `resolve_tracking_db_path()` and `resolve_algo_db_path()` in `src/db_factory.py`.
- Enforced strict fail-closed policy (raising `DatabaseContaminationError`) when executing in a non-production context where a production path might be accessed.

## 5. Next Steps
- Begin gradual rollout in `staging`.
- Observe error counts from `QualityGateError` to ensure true hallucination attempts are safely pruned.
