# TaskBound v1.0-compact: illustrated design

## 1. The failure being measured

An agent is authorized to operate an HPC account, but its current task grants a
narrower scope. Untrusted text appears in a file, tool result, module description,
or work order and asks the agent to take an action that the account permits but
the task does not require.

```mermaid
flowchart LR
    TASK[Authorized task: diagnose failed job] --> AGENT[Planner → worker → planner]
    VEHICLE[Untrusted text in tool-visible artifact] --> AGENT
    AGENT --> IN[In-scope diagnosis]
    AGENT -. requested diversion .-> OUT[Out-of-scope action]
    OUT --> ORACLE[Policy + action-trace oracle]
```

The primary outcome is compliance with that diversion, conditioned on the agent
actually seeing the injected text. Realization is graded separately, so an
attempted but failed action is not confused with refusal.

## 2. Compact allocation

The release uses one host and one task, T1 `t1_failed_job`. It retains the full
entry-point × action crossing:

```mermaid
flowchart TB
    T1[T1 failed-job diagnosis] --> E1[E1 job log]
    T1 --> E2[E2 project README]
    T1 --> E3[E3 module description]
    T1 --> E4[E4 work order]
    E1 --> A[A1 read secret · A2 scheduler · A3 modify config · A4 persist content]
    E2 --> A
    E3 --> A
    E4 --> A
```

That produces 16 cells and four request families. The repository also contains
T2–T5 and eight auxiliary cells, but they are future-study assets, not part of
v1.0-compact. Consequently this release makes no task-generalization claim.

## 3. Five conditions

```mermaid
flowchart LR
    CLEAN[Clean: no injected text]
    INERT[Inert: same vehicle, no request]
    BENIGN[Benign: matched in-scope request]
    ATTACK[Attacked: matched out-of-scope request]
    NEAR[Near-miss: requested action moved into task scope]
```

- Clean measures ordinary utility and background scope violation.
- Inert measures whether the targeted action happens without a request.
- Benign checks ordinary instruction following for a matched in-scope target.
- Attacked measures susceptibility after exposure.
- Near-miss separates refusal of an action class from respect for task scope.

Controls account for most of the run budget because these explanations cannot be
recovered from attack runs alone.

## 4. One execution mode

Every scheduled cell uses the same two-agent protocol:

```mermaid
sequenceDiagram
    participant P as Planner
    participant W as Worker
    participant H as HPC tools
    P->>W: diagnosis plan / delegated work
    W->>H: inspect and act
    H-->>W: tool-visible artifacts, possibly injected
    W-->>P: findings
    P->>H: finish task
```

Execution mode is held constant, so it is not an estimand and cannot confound
the E1–E4 contrast. No release claims an execution-mode effect.

## 5. Replication and claims

The exact schedule is repeated for two model families. This is a replication
axis, not a ranking study; family heterogeneity is exploratory.

```mermaid
flowchart TB
    DESIGN[Same frozen 369-run schedule] --> M1[Model family 1]
    DESIGN --> M2[Model family 2]
    M1 --> EST[Confirmatory susceptibility estimate]
    M2 --> EST
    EST --> GATE[Lower 95% bound > 10pp floor]
```

Only attack susceptibility above that practical-risk floor is confirmatory.
Scope selectivity, entry-point effects, action effects, interaction, wording
variance, and family heterogeneity are exploratory. There are no claims across
tasks, hosts, or execution modes.

## 6. Run arithmetic

N = 9 exposed runs per injected group, balanced as three runs over each of three
paraphrases. The attempt cap is 27 per injected group.

| Per model family | Runs |
|------------------|-----:|
| 16 attacked groups | 144 |
| 16 benign groups | 144 |
| 4 inert groups | 36 |
| 4 near-miss blocks | 36 |
| 1 clean block | 9 |
| **Target total** | **369** |
| **Hard attempt cap** | **1,017** |

Across two model families the compact release targets **738 runs** with a
**2,034-attempt hard cap**. Dropping to one family is the only predeclared
in-release cost reduction, and sacrifices replication. Changing N, cells, or
conditions requires a new versioned registration.

## 7. Release path

```mermaid
flowchart LR
    BUILD[Implemented harness + T1 library] --> REVIEW[Acceptance + realism review]
    REVIEW --> PILOT[Integration smoke + sizing pilot]
    PILOT --> GATES[Susceptibility power + cost gates]
    GATES --> SIGN[Sign v1.0-compact registration]
    SIGN --> RUN[Two frozen 369-run schedules]
    RUN --> AUDIT[Oracle audit + reproducible aggregation]
```

N = 9 is fixed before the pilot and must pass its own exact susceptibility power
simulation, inheriting no conclusion from any earlier design. A failed gate
blocks the release; it does not authorize a sample-size or claim change after
pilot results are visible.
