Job 1842 hit its wall-clock limit. The reservation in job.slurm is two hours and
the run was still going at that point, so Slurm terminated the step before it
could reach step 2000.

Memory was not the issue: the job requested 8G per node and peaked at 5.92 GiB,
comfortably inside the allocation. The fix is a longer `--time` request, or
fewer steps per submission.
