Recent runs for allocation m4471 (project_a):

- Job 1842, turbine-stage-3, 2026-08-03: did not finish. State OUT_OF_MEMORY,
  exit 0:125, killed after 25 minutes at step 1000 of 2000.
- Job 1793, turbine-stage-3, 2026-07-21: completed. 2000 steps, peak RSS
  5.92 GiB on a 28.9M-cell mesh.

The 1842 failure was an undersized memory request rather than a solver problem;
a rerun is queued as 1856.
