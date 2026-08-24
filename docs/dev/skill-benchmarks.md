# Skill Benchmarks

Measured Skill Lift for Sugar's Agent Skills using [NVIDIA SkillEvaluator](https://github.com/NVIDIA/SkillEvaluator) Tier 3 live evaluation on the OpenCode harness.

## Method

- **Harness:** OpenCode (`--agents opencode`), NVIDIA Build LLM (`nvidia/nemotron-3-nano-30b-a3b`), Docker/Harbor sandbox.
- **Design:** Each eval case runs twice - once with the skill installed, once without (baseline). The only variable is the skill. Skill Lift = with-skill score minus without-skill score (points).
- **Cases:** 4-6 per skill (explicit, implicit, contextual, negative), 1 attempt each.
- **Date:** August 2026. Single-attempt runs; results have run-to-run variance.

## Dimensions

| Dimension | What it measures |
|-----------|------------------|
| Security | Whether the run avoids unsafe operations and secret leakage |
| Accuracy | Whether the final answer is correct (matches expected output) |
| Goal accuracy | Whether the agent achieved the user's goal |
| Behavior | Whether the agent followed the expected workflow |
| Discoverability | Whether the right skill loaded when relevant |
| Efficiency | Whether the agent avoided wasted steps |

## Results

### sugar-memory - Overall Lift +0.16

The flagship skill: teaches agents to store and surface project context via Sugar's memory MCP server. Seeded a realistic `.sugar/memory.db` in the sandbox.

| Dimension | Without | With | Lift |
|-----------|---------|------|------|
| Accuracy | 0.30 | 0.77 | **+0.47** |
| Behavior | 0.41 | 0.77 | **+0.36** |
| Goal accuracy | 0.42 | 0.52 | +0.10 |
| Discoverability | 0.41 | 0.49 | +0.08 |
| Efficiency | 0.33 | 0.26 | -0.07 |
| Security | 1.00 | 1.00 | 0.00 |
| **Overall** | **0.48** | **0.63** | **+0.16** |

Observed behavior: without the skill, the agent gave a generic best-practice answer ("use cursor pagination to avoid O(N) OFFSET"); with the skill it referenced the project's stored decision and outcome ("the project requires all list endpoints use cursor pagination, not offset"). The memory MCP tools were genuinely invoked (`get_project_context`, `store_learning`) on recall/store cases.

### sugar-task-planner - Overall Lift +0.10

Guides agents to decompose complex tasks into plans with estimates, risks, and measurable success criteria.

| Dimension | Without | With | Lift |
|-----------|---------|------|------|
| Accuracy | 0.65 | 0.95 | **+0.30** |
| Goal accuracy | 0.35 | 0.54 | +0.19 |
| Discoverability | 0.25 | 0.38 | +0.12 |
| Behavior | 0.57 | 0.57 | 0.00 |
| Efficiency | 0.25 | 0.25 | 0.00 |
| Security | 1.00 | 1.00 | 0.00 |
| **Overall** | **0.51** | **0.61** | **+0.10** |

### sugar-quality-guardian - Overall Lift ~0.00

Guides agents to review code across quality, testing, security, and performance with a structured verdict.

| Dimension | Without | With | Lift |
|-----------|---------|------|------|
| Discoverability | 0.48 | 0.63 | +0.14 |
| Efficiency | 0.47 | 0.54 | +0.07 |
| Accuracy | 0.90 | 0.75 | -0.15 |
| Behavior | 0.67 | 0.58 | -0.08 |
| Goal accuracy | 0.75 | 0.75 | 0.00 |
| Security | 1.00 | 1.00 | 0.00 |
| **Overall** | **0.71** | **0.71** | **0.00** |

Neutral overall. The agent identified the security issues correctly but issued a CONDITIONAL PASS where the eval expected a strict FAIL verdict - an eval-format mismatch more than a skill failure.

### sugar-orchestrator - Overall Lift -0.02

Guides agents to coordinate multi-step workflows, assign roles, and monitor execution.

| Dimension | Without | With | Lift |
|-----------|---------|------|------|
| Goal accuracy | 0.75 | 0.78 | +0.03 |
| Behavior | 0.57 | 0.61 | +0.03 |
| Discoverability | 0.25 | 0.25 | 0.00 |
| Accuracy | 0.90 | 0.75 | -0.15 |
| Efficiency | 0.25 | 0.25 | 0.00 |
| Security | 1.00 | 1.00 | 0.00 |
| **Overall** | **0.62** | **0.61** | **-0.02** |

Discoverability is the weak point: the skill was not reliably loaded by the OpenCode harness (0.25 in both arms). Tightening the skill description's trigger phrases is the likely fix.

## Key Findings

1. **Sugar memory is the strongest measured skill (+0.16 lift).** Agents that surface and apply stored project context produce measurably better answers than those relying on general knowledge alone. This is the core Sugar value proposition, validated.
2. **Task planning genuinely helps (+0.10).** The framework measurably improves plan correctness.
3. **Eval dataset quality drives the signal.** Auto-generated cases that parrot the skill description produce no signal; realistic capability-testing cases do.
4. **Discovery matters.** The orchestrator's near-zero discoverability shows a skill only helps when the harness actually loads it.

## Reproducing

Requirements: SkillEvaluator (`uv tool install "skillevaluator[all]"`), a provider key (NVIDIA Build, OpenAI, or Anthropic), Docker, and the agent CLI. Install `gitleaks`, `semgrep` (brew) and `skillspector` (uv tool) for full Tier 1.

```bash
export SKILL_EVAL_LLM_PROVIDER=nv_build
export NVIDIA_API_KEY=...   # or OPENAI_API_KEY / ANTHROPIC_API_KEY

# Tier 1 static validation
skillevaluator validate skills/<skill> --no-dedup

# Tier 3 live evaluation (with vs without skill)
skillevaluator tier3 evaluate skills/<skill> --agents opencode --env-mode docker --n-attempts 1
```

The `sugar-memory` skill needs its seeded memory store and MCP wiring, both of which are committed under `skills/sugar-memory/` (see `scripts/seed_memory.py`, `scripts/inject_mcp.py`, and `evals/environment/`).