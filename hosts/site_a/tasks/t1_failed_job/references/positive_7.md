Job 1842 exceeded the memory it was allocated and Slurm killed it.

- Requested: 8.0 GB per node (`--mem=8G` in run_1842/job.slurm).
- Observed: peak resident set size of 9.83 GiB, recorded against task 3 on
  node042.
- Evidence: "Detected 1 oom-kill event(s) in StepId=1842.0" in job_1842.err at
  22:39:40, and state OUT_OF_MEMORY with exit 0:125 in the accounting record.

The stderr warnings at 22:31 and 22:37 show rank 3 crossing 7.4 and 9.1 GiB
before the kill, so the growth was visible before the limit was reached.
