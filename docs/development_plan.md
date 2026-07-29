# TaskBound Benchmark Development Plan

## Purpose

TaskBound will evaluate whether LLM agents working in HPC-like environments can complete useful tasks without being redirected by adversarial content found in logs, files, tool output, or peer-agent messages.

The first development version should stay simple. It should not try to reproduce a full HPC center or follow AgentDojo's application-heavy structure. Instead, it should provide a small, clear benchmark harness with HPC-specific tasks, task-scoped policies, and deterministic security checks.

## Core Idea

Each benchmark case should contain:

- a user task;
- a simulated HPC workspace;
- one or more agent tools;
- optional adversarial content;
- a task policy describing what the agent is allowed to do for this task;
- an oracle that decides whether the task succeeded and whether the agent violated scope.

The key security question is not whether the agent has valid credentials. In TaskBound, the agent is assumed to act with the user's legitimate access. The question is whether the agent stays inside the delegated task.

## Execution Modes

TaskBound should be designed around two execution modes:

- **Local simulation mode:** the first release target. All scheduler, filesystem, module, and project behavior is simulated inside an isolated local workspace.
- **Real HPC mode:** a later release target. The same benchmark concepts run against an actual HPC system through a carefully controlled site adapter.

The benchmark should therefore avoid baking local-only assumptions into scenarios. Scenarios should describe intent, resources, policies, and expected outcomes. Backends should decide how those concepts are implemented.

The first release should implement only local simulation mode, but the interfaces should leave room for real execution later.

## Initial Scope

The first version should focus on a local, reproducible benchmark rather than a live cluster.

Recommended initial surfaces:

- shared filesystem poisoning;
- Slurm-like job inspection and submission;
- cross-project data leakage;
- poisoned tool or module descriptions;
- silent scientific-result modification.

Deferred surfaces:

- real scheduler integration;
- multi-node execution;
- real HPC account systems;
- complex multi-agent workflows;
- production-grade defenses.

These can be added after the basic benchmark format is stable.

## Development Approach

### Phase 1: Minimal Benchmark Harness

Build a command-line runner that can execute one benchmark case at a time.

The runner should:

- load a scenario definition;
- create an isolated temporary workspace;
- expose a small set of tools to the agent;
- collect the agent's actions;
- run utility and security checks;
- write a machine-readable result file.

The harness should be intentionally boring and transparent. A developer should be able to inspect a scenario directory and understand what is being tested.

The runner should call a backend interface rather than directly assuming local simulation. For the first release, that interface can have only one implementation: `local_sim`. Later, a `real_hpc` implementation can provide the same operations using site-approved Slurm commands, filesystem paths, and monitoring hooks.

### Phase 2: Scenario Format

Define a simple scenario layout, for example:

```text
scenario-name/
  task.md
  policy.yaml
  workspace/
  attacks/
  oracle.py
```

The format should make task boundaries explicit. `policy.yaml` should describe allowed paths, allowed projects, allowed scheduler actions, and allowed egress targets at a high level. `oracle.py` should check the final state deterministically.

Avoid a large framework at this stage. A small convention plus validation is enough.

### Phase 3: Local HPC Simulation

Create lightweight local tools that behave like common HPC interfaces:

- `squeue`;
- `sbatch`;
- `sacct`;
- `scancel`;
- `module`;
- filesystem read/write helpers.

These tools do not need to fully implement Slurm or environment modules. They only need enough behavior to support benchmark scenarios and expose realistic failure modes.

The simulator should record all tool calls in an action log. Security checks should use this log and the final workspace state.

This local simulator should be treated as the reference backend for development and CI. It should define the benchmark's expected semantics before any real HPC integration is attempted.

### Phase 3b: Backend Interface For Future Real HPC Runs

Define a small backend boundary early, even if only the local backend exists at first.

Suggested backend operations:

- create and destroy a run workspace;
- resolve scenario paths to backend-specific paths;
- read, write, and list files under policy control;
- inspect scheduler state;
- submit or cancel jobs when the task policy allows it;
- inspect modules or tools;
- collect action logs and final state.

The real HPC backend should be added only after the local benchmark is stable. It should require explicit configuration and should default to conservative behavior:

- no real credentials stored in scenarios;
- no real protected data in benchmark workspaces;
- site-specific allowlists for queues, projects, partitions, and paths;
- dry-run mode where possible;
- strict cleanup of submitted jobs and temporary files;
- operator-visible logs for every action.

This keeps TaskBound portable: scenarios remain mostly backend-independent, while the backend controls how risky operations are implemented.

### Phase 4: Seed Scenarios

Start with a small set of clear scenarios before adding many variants.

Suggested first cases:

- **Failed Job Diagnosis:** The agent reads a poisoned stderr log while debugging a failed Slurm job.
- **Project Boundary:** The agent is asked to summarize files from project A while adversarial content asks it to inspect project B.
- **Result Integrity:** The agent is asked to repair a workflow but poisoned output asks it to change scientific parameters.
- **Module Poisoning:** A fake module description or helper tool tries to redirect the agent to an unsafe action.
- **Allocation Abuse:** The agent is asked to inspect job status but is induced to submit or resubmit jobs.

Each case should have one clean version and one attacked version.

### Phase 5: Metrics And Reporting

The first reporting format should be simple:

- task success;
- security violation;
- violated policy rule, if any;
- action trace;
- scenario metadata;
- model or agent configuration.

The main benchmark result can be summarized as:

- utility rate on clean tasks;
- utility rate under attack;
- attack success rate;
- refusal or overblocking rate.

This is enough for early comparison without overdesigning the scoring system.

## Repository Structure

Recommended early structure:

```text
taskbound/
  backends/
    local_sim/
    real_hpc/
  runner/
  tools/
  policies/
  scenarios/
  oracles/
  results/
docs/
  development_plan.md
```

The exact package layout can change later. The important early decision is to keep benchmark cases separate from the runner so scenarios remain easy to read and review.

## Design Principles

- Prefer deterministic checks over manual judgment.
- Keep task policy explicit and close to the scenario.
- Model HPC concepts only as deeply as needed for the security behavior.
- Treat all agent-visible environment content as potentially adversarial.
- Record enough action history to explain why a run passed or failed.
- Make it easy to add one new scenario without touching the core runner.

## Early Milestones

1. Define the scenario schema.
2. Implement the local runner and action logger.
3. Implement the minimal Slurm-like and filesystem tools.
4. Add the first five seed scenarios.
5. Produce JSON results and a short human-readable summary.
6. Run a baseline agent on clean and attacked versions.
7. Review failures and decide which surfaces need deeper modeling.

## Open Questions For The Next Planning Round

- How strict should task policies be for exploratory scientific work?
- Should the benchmark support multiple agent frameworks immediately, or start with one adapter?
- What level of Slurm fidelity is necessary for credible evaluation?
- Should defenses be included in the first release, or should the first release only measure baseline vulnerability?
- How should scientific-integrity violations be represented across different domains?

## Near-Term Definition Of Done

The first useful TaskBound release should run locally, include a small set of representative HPC-agent security scenarios, and produce repeatable utility and security results. It does not need to be comprehensive. It only needs to make the hijacked-authorized-agent failure mode concrete enough that later versions can deepen the scenarios, policies, and defenses.
