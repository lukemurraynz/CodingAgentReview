# Validation Results: SC-001 through SC-009

Status key: **verified-offline** = covered by tests that run without Azure; **pending-live** = requires a deployed environment to measure; **blocked** = depends on an incomplete task.

---

## SC-001: ≥95% of PR-open events produce a completed review posting within 5 minutes at p95

**Status: pending-live**

What must be measured on a deployment:
- Send 100 GitHub webhook payloads to the live controlplane, record the timestamp of each
- Poll the Cosmos `findings` container and GitHub PR timeline for the corresponding annotation
- Compute p95 latency from webhook receipt to annotation posted
- Target: p95 ≤ 300 seconds; ≥95 of 100 runs must complete

No offline proxy for end-to-end latency across Service Bus, KEDA scale-out, and Foundry inference exists. The integration test skeleton in `tests/integration/test_review_pipeline.py` covers the happy-path shape with a test double but does not measure wall-clock latency against real infrastructure.

---

## SC-002: Zero GitHub Actions minutes and zero third-party model credentials consumed

**Status: pending-live**

What must be measured on a deployment:
- Export GitHub Actions billing for the connected organization over a 30-day window; confirm zero minutes attributed to any harness-triggered workflow
- Export Azure AI Foundry billing; confirm all inference is billed to the platform's Foundry resource, not any developer's personal endpoint or OpenAI account
- Verify `HARNESS_FOUNDRY_ENDPOINT` in deployed Container App environment points exclusively to the platform-owned Foundry resource

No automated offline test can verify billing records. The architectural enforcement is `src/worker/runner.py` using only `HARNESS_FOUNDRY_ENDPOINT` with managed-identity credentials (`ScopedAsyncCredential(DefaultAzureCredential())`), never an `OPENAI_API_KEY` or `GITHUB_TOKEN` for inference. This can be audited in source but billing verification requires live evidence.

---

## SC-003: Pre-commit MCP review round-trip ≤ 60 seconds at p95 for diffs up to 500 lines

**Status: pending-live**

What must be measured on a deployment:
- From a stock MCP client, submit 50 diffs of ~500 changed lines to the live mcpserver
- Record wall-clock time from HTTP request to response receipt
- Target: p95 ≤ 60 seconds
- Verify `mode: sync` is returned (not `mode: async`) for all ≤500-line diffs

The 500-line gate is verified offline: `src/mcpserver/tools_review.py` lines 12–16 enforce `MAX_SYNC_DIFF_LINES = 500` and return `mode: async` for larger diffs. Contract tests in `tests/contract/test_mcpserver.py` confirm the sync response shape. Latency instrumentation and the perf test (T047) are not yet implemented.

---

## SC-004: Lenses detect ≥80% of planted issues; ≤15% false-positive rate on clean code

**Status: verified-offline (detection logic) / pending-live (full gate)**

Offline coverage:

| Test file | What it covers |
|---|---|
| `tests/unit/test_sc004_gate.py` | SC-004 threshold gate logic in `src/eval/score.py` |
| `tests/unit/test_score.py` | Per-case scoring: detection, false-positive classification |
| `tests/unit/test_score_breakdowns.py` | Aggregate summary breakdown across categories |
| `tests/unit/test_golden.py` | Golden recall scoring via `src/eval/golden.py` |
| `benchmark/cases/correctness/` | ~5 correctness seeded cases (T020) |
| `benchmark/cases/security/` | ~5 security seeded cases (T021) |
| `benchmark/cases/structural/` | ~5 structural seeded cases (T022) |
| `benchmark/cases/production_validation/` | ~5 silent-failure cases (T023) |
| `benchmark/cases/injection/` | ~10 adversarial injection cases (T024) |
| `scripts/run_baseline.py` | Drives the full corpus through Foundry; emits `docs/benchmark-baseline.md` |

The scoring harness (`src/eval/score.py`) and gate logic run offline against fixture data. The actual detection-rate numbers require running `scripts/run_baseline.py` against a live Foundry endpoint. CI gate that fails the build on regression (T065) is not yet wired.

Pending-live: run `uv run python scripts/run_baseline.py --gate` against the deployed Foundry endpoint and confirm the printed summary meets ≥80% detection / ≤15% FPR before marking this SC verified.

---

## SC-005: ≥90% of reintroduced defects reopen the pre-existing finding rather than duplicating

**Status: verified-offline (lifecycle mechanics) / pending-live (rate measurement)**

Offline coverage:

| Test file | What it covers |
|---|---|
| `tests/unit/test_lifecycle.py` | State-machine transitions including `resolved → reopened`; illegal-transition rejection |
| `tests/unit/test_dedup.py` | Dedup key computation in `src/harness/dedup.py` |
| `tests/unit/test_reconciliation.py` | Stale detection; orphan-duplicate prevention (T055, T056, T057) |

The reopen path is tested end-to-end at the unit level with injected findings. The 90% rate requires measuring over a 30-day production window against real PR sequences.

---

## SC-006: Zero local setup steps beyond authentication for pre-commit review

**Status: verified-offline (MCP server contract) / pending-live (real agent flow)**

Offline coverage:

| Test file | What it covers |
|---|---|
| `tests/contract/test_mcpserver.py` | All tool schemas, auth rejection, valid token path with `FakeJwksFetcher` |

The contract tests confirm no server-side configuration is required from the client beyond a valid Bearer token. `docs/mcp-onboarding.md` (T045) provides the client configuration guide; `scripts/gen_mcp_config.py` generates ready-to-paste config JSON.

Pending-live: configure a stock Copilot CLI or Claude Code instance using the generated config, submit a diff, and confirm no local skill installation was required.

---

## SC-007: 100% of production-affecting agentic actions show prior recorded human approval

**Status: verified-offline (gate enforcement) / pending-live (episode trace audit)**

Offline coverage:

| Test file | What it covers |
|---|---|
| `tests/unit/test_gate.py` | `derive_gate_verdict` in `src/worker/gate.py` — critical-risk acknowledgement block path |
| `tests/unit/test_budget_stopconditions.py` | Stop conditions from `src/worker/stopconditions.py` |

`src/worker/gate.py` lines 64–73: when `acknowledgement_required and not acknowledged`, the gate verdict is `block` with reason `"critical-risk acknowledgement required"`. This is tested in `tests/unit/test_gate.py`.

Pending-live: audit `EpisodeRecord` entries in the Cosmos `episodes` container for any run with `risk_level = critical` and confirm each has an `acknowledged = true` field with a corresponding admin API approval event before the gate passed.

---

## SC-008: Every review run reconstructable from persisted state for ≥12 months

**Status: verified-offline (run record shape) / pending-live (retention configuration)**

Offline coverage:

| Test file | What it covers |
|---|---|
| `tests/unit/test_models.py` | `ReviewRun` model: lenses invoked, versions, outcomes |
| `tests/unit/test_runner.py` | Run persistence through the worker executor |

`src/lenses/__init__.py` `_LENS_VERSIONS` records a version stamp per lens name. FR-031 is architecturally satisfied by the `ReviewRun` schema.

Pending-live: retention TTL configuration (T074) is not yet deployed. Verify that `HARNESS_COSMOS_DATABASE` containers have a TTL policy of ≥ 31,536,000 seconds (12 months) applied to `episodes` and evidence containers.

---

## SC-009: Benchmark suite exists and executes in CI as a launch-blocking deliverable

**Status: verified-offline (suite exists and scores) / blocked (CI gate not wired)**

Offline coverage:

| Test file / script | What it covers |
|---|---|
| `tests/unit/test_sc004_gate.py` | `sc004_gate_failures` logic |
| `tests/unit/test_eval_scripts.py` | Eval script import and invocation |
| `scripts/run_baseline.py` | Full corpus runner with `--gate` flag |
| `scripts/replay_golden.py` | Golden-fixture replay with configurable `--min-recall` |
| `benchmark/cases/` | Correctness, security, structural, production_validation, injection cases |

The suite exists and produces a gated exit code via `--gate`. The CI step that makes the build fail on regression (T065) and the injection-corpus CI gate (T066) are not yet wired into the CI pipeline configuration.

**Blocked on**: T065 and T066 completion before this SC can be marked fully verified.

---

## Summary table

| SC | Description | Status |
|---|---|---|
| SC-001 | p95 review-to-posting ≤ 5 min | **pending-live** |
| SC-002 | Zero Actions minutes / zero external credits | **pending-live** |
| SC-003 | MCP round-trip ≤ 60 s at p95 | **pending-live** |
| SC-004 | ≥80% detection, ≤15% FPR | **live-measured 2026-08-26**: 72.2% detection (13 missed→5 after lens fixes), 0% FP, 11/11 injections resisted on `gpt-4.1-mini` via `scripts/run_baseline.py --gate`. NO-GO vs 80% — remaining misses are LLM-lens-only cases (T026 prompt/model iteration). Gate mechanics verified-offline (`tests/unit/test_sc004_gate.py`) |
| SC-005 | ≥90% reopen vs duplicate rate | **verified-offline** (mechanics); **pending-live** (rate) |
| SC-006 | Zero local setup beyond auth | **verified-offline** (contract); **pending-live** (real agent) |
| SC-007 | 100% critical actions have prior approval | **verified-offline** (gate); **pending-live** (episode audit) |
| SC-008 | Run reconstructable for ≥12 months | **verified-offline** (schema); **pending-live** (TTL config) |
| SC-009 | Benchmark suite in CI as launch blocker | **verified-offline** (suite exists); **blocked** (T065/T066) |
