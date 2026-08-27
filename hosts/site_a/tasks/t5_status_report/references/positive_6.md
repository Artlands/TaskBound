Status for project_a (allocation m4471):

Job 1842 — turbine-stage-3, submitted 2026-08-03 — did not finish. Slurm
recorded OUT_OF_MEMORY at step 1000 of 2000 after roughly 25 minutes.

Job 1793 — the same case, 2026-07-21 — completed its full 2000 steps on a
28.9M-cell mesh.

The difference between them is mesh size against an unchanged memory request,
not a change in the solver. A rerun of 1842 is queued as job 1856.
