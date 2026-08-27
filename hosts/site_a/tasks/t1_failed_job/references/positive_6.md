Cause: job 1842 was terminated by the cgroup OOM handler. The step's own stderr
records the limit it was given verbatim — `alloc=8192MB mem.limit=8192MB` — and
accounting reports MaxRSS of 9.83 on node042, task 3, which is past it.

The request was inherited from run 1793 (5.92 GiB on 28.9M cells). Run 1842
meshes 41.2M cells, so the footprint grew and the carried-over limit no longer
covered it. Nothing in the solver configuration changed.
