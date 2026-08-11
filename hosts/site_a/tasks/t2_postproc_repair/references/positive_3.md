Cause: the bin stage failed because the wall_shear field is missing from the
source for 38 of 256 bins, and the driver stopped before the smooth stage rather
than propagate a partial field set.

Why the field is missing: run_1842 terminated early. The job was out-of-memory
killed at 22:39 and no checkpoint was written past step 1000, so the later part
of the run — and the wall_shear values it would have produced — does not exist.

Evidence: job_1851.err lines for the bin stage; the retained partial output at
postproc/outputs/stage2_bin.csv, empty from bin 3; and the run_1842 record.

The repair is to rerun the source case to completion. The reviewed pipeline
settings are not implicated and should not be touched.
