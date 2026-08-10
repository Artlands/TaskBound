Cause: the run was killed by the cgroup out-of-memory handler on node042 (task
3), and Slurm recorded state OUT_OF_MEMORY with exit code 0:125.

The batch script requests 8G per node. The request should be raised before the
next submission, and the run restarted from the last checkpoint at step 500.
