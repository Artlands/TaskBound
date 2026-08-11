Stage 2, the binning stage, is where it stopped. The driver log for job 1851
shows the extract stage completing normally (2.1M sampled points) and then bin
failing on a missing field: wall_shear is absent for 38 of the 256 bins.

That is a consequence of the source run rather than of the post-processing. Run
1842 did not finish — it was killed by the out-of-memory handler and left no
checkpoint past step 1000 — so the solver never wrote wall_shear across the full
domain. outputs/stage2_bin.csv retains the partial result and is empty from bin
3 on.

Nothing in pipeline.json is wrong. The fix is a complete source run, not a
different bin count or smoothing window.
