The binning stage is where the pipeline stops. job_1851.err shows the driver
reporting that stage bin returned 1 and halting before the smooth stage, because
the wall shear field is absent from the source for 38 of 256 bins.

That absence is inherited, not introduced here: run 1842 did not complete — it
was killed for memory and left no checkpoint past step 1000 — so the field was
never written for the back half of the domain. outputs/stage2_bin.csv shows the
empty columns from bin 3 onward.

No pipeline setting will fix an incomplete input. bin_count and smoothing_window
stay as reviewed; the stage runs cleanly once 1842 has been rerun in full.
