Job 1842 was killed by the cgroup out-of-memory handler. sacct records the job
in state OUT_OF_MEMORY with exit code 0:125, and job_1842.err has
"Detected 1 oom-kill event(s) in StepId=1842.0" followed by
"srun: error: node042: task 3: Out Of Memory" at 22:39:40.

The cause is an undersized memory request. job.slurm asks for `--mem=8G`, but
accounting reports MaxRSS of 9.83G on node042, task 3. The request was carried
over from run 1793, which peaked at 5.92 GiB on a 28.9M-cell mesh; run 1842 uses
a 41.2M-cell mesh, so the footprint grew past the limit. The stderr warnings at
22:31 and 22:37 show rank 3 climbing through 7.4 and 9.1 GiB before the kill.
