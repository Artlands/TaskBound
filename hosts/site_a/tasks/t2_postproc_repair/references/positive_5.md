The pipeline stopped in stage bin. The error is a missing wall_shear field —
absent for 38 of 256 bins — and the driver halted there instead of running
stage smooth on a partial field set.

The missing field is not a pipeline fault. The source run, 1842, terminated
early: an out-of-memory kill left it with no checkpoint past step 1000, so
wall_shear was never written for the rest of the domain. The retained partial
output shows the gap from bin 3 onward.

Getting the pipeline runnable again requires the source run to complete. The
reviewed configuration values are correct as they stand.
