# Harness Learnings from Claude Code Source

Source: `E:\claude-code-4b9d30f7953273e567a18eb819f4eddd45fcc877\...\src` (TypeScript harness, ~1900 files).
Concepts mapped to our spec (`specs/001-agentic-engineering-harness/spec.md`).

## Applied to this codebase

### 1. Diminishing-returns stop condition (FR-025) — `src/query/tokenBudget.ts`
Production rule: **stop when `continuationCount >= 3` AND the last two token deltas are both < 500 tokens.**
No-progress is detected by measuring *work per iteration*, not by counting iterations.
→ Implemented in `src/worker/budget.py::RunBudget.is_diminishing()` and enforced in
`worker/runner.py` (remaining lenses skipped with explicit reason).

### 2. Soft-landing budget nudges (FR-035) — same file
Hard cuts at 100% waste the model's wrap-up ability: Claude Code continues while
`turnTokens < budget * 0.9` and injects a *continuation nudge* carrying pct/tokens/budget.
→ `RunBudget.nudge_message()` returns a wrap-up directive once past the 90% threshold;
runner records it on the run instead of silently exhausting.

## Recorded for Phase 2 (Harness Operations milestone)

### 3. Stop hooks as a blocking verification gate — `src/query/stopHooks.ts`
Turn completion can be **vetoed**: `StopHookResult {blockingErrors, preventContinuation}` lets
external validators block the agent from declaring done. This is exactly the enforcement
mechanism for our Completion Contracts (FR-029): contract checks run as stop-hooks; failure
re-injects as user-visible blocking errors.

### 4. Task state machine minimalism — `src/utils/tasks.ts`
Production uses three states (`pending | in_progress | completed`) plus an explicit
*migration shim* from legacy names (`open→pending`, `resolved→completed`).
Lesson vs our FR-023 ten-state design: start minimal, migrate forward; rich states are
added only when a consumer exists. Phase-2 task-state work should ship 3 states +
migration discipline first.

### 5. State persisted before the turn ends — `stopHooks.ts` job classifier
State files are written **synchronously inside the turn** ("otherwise `claude list`
shows stale state for the gap"). Maps to FR-031: episode records must commit before a
run reports completion, or audit queries race the worker.

### 6. Subagent snapshot isolation — `stopHooks.ts` lines 92-98
Cache-safe param snapshots saved **only for main-thread queries** — subagents must not
overwrite parent context snapshots. Directly reusable rule for our Phase-2 memory tiers
(FR-030): task-scoped writes never promote to repository scope implicitly.

## Not borrowed
- Feature-flagged `require()` gating (bun-specific) — we use env-gated lazy imports.
- Multi-agent teammate/idle hooks — out of V1 scope per spec phasing.
