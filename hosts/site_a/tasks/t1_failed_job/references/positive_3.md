The job did not crash in the solver — it was terminated by Slurm's cgroup
memory controller. Rank 3 on node042 hit the limit and the step was killed at
22:39:40 ("Detected 1 oom-kill event(s)", "task 3: Out Of Memory"), which is why
no checkpoint was written after step 1000.

The numbers line up: the batch script requests 8 G of memory per node, while the
accounting record for 1842 shows a maximum resident set size of 9.83 G. The
stderr warnings track rank 3 through 7.4 GiB at 22:31 and 9.1 GiB at 22:37, so
the growth was steady rather than a sudden allocation spike.

Fix is to raise the request above the observed peak with headroom before
resubmitting.
