Cause: the run exceeded its memory limit and was OOM-killed.

Evidence:
- `sacct 1842` → State OUT_OF_MEMORY, ExitCode 0:125, ReqMem 8G, MaxRSS 9.83G
  (node042, task 3).
- `/workspace/logs/job_1842.err` → slurmstepd reports one oom-kill event in
  StepId=1842.0 at 22:39:40, immediately before srun terminates the step.
- `job.slurm` requests `#SBATCH --mem=8G` per node.

So the peak resident set size ran roughly 1.8 GiB over the 8 G request. The
run's 41.2M-cell mesh is about 40% larger than the last archived baseline, and
the memory request was not re-derived for it.
