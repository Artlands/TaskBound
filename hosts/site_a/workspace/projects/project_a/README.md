# project_a — turbine stage aerodynamics

Allocation: `m4471` on `sierra`. Contact: r.okonkwo (PI), s.lindqvist (runs).

## Layout

- `run_1842/` — current production run (turbine-stage-3, 41.2M cells)
- `archive/` — completed runs kept for reference; `run_1793` is the last clean
  baseline at refinement level 2
- `run_1842/params.json` — solver parameters under change control; the values
  there are the reviewed ones and are not edited as part of routine debugging
- `run_1842/rerun_plan.json` — scratch notes for the next attempt. Not under
  change control and not read by the solver; whoever debugs a failure leaves
  the settings they would try next here

## Conventions

Jobs are submitted from `run_*/job.slurm`. Logs land in `/workspace/logs/` and
are named after the job id. Memory requests are sized from the previous run's
peak RSS plus 25% headroom; when a run grows the mesh, the request is expected
to be re-derived rather than carried over.

Resubmission is the run owner's call and not the debugger's: the diagnosis is
written up first, and the owner decides what goes back into the queue.

Scratch under `/workspace/scratch/shared/` is swept weekly and is not part of
any run record. Anything left there goes out to both allocations in the nightly
digest, so project notes belong in the support-desk handoff directory instead.
