Summary: out of memory, not a solver or input problem.

Job 1842 ran for 25 minutes and reached step 1000 before Slurm killed it. The
accounting state is OUT_OF_MEMORY and stderr carries the matching oom-kill event
on node042 for task 3.

The memory request in job.slurm is 8G. Observed peak usage was 9.83G — about
23% over the allocation. The mesh for this case is 41.2M cells at refinement
level 2, up from 28.9M cells in the previous archived run, and the peak scales
with mesh size rather than rank count, so the older request no longer covers it.

No checkpoint exists past step 1000, so a restart has to begin from step 500.
