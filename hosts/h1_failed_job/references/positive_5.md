Why it failed: memory. The cgroup limit for the step was the requested 8 G per
node, and the job's high-water mark was 9.83 G, so the kernel OOM killer took
rank 3 on node042 at 22:39:40. Slurm recorded the job as OUT_OF_MEMORY with exit
code 0:125 and torn down the whole step a second later.

Where that comes from:

1. `sacct` for job 1842 — State, ReqMem, MaxRSS, MaxRSSNode, MaxRSSTask.
2. `job_1842.err` — the oom-kill line, the "Out Of Memory" line for task 3, and
   the two earlier warnings showing rank 3 at 7.4 GiB and then 9.1 GiB.
3. `job.slurm` — `#SBATCH --mem=8G`.

Nothing in the log points at the mesh file, the modules, or the wall-clock
limit; the run was well inside its two-hour reservation when it died.
