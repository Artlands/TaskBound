The pipeline halted at the bin stage. job_1851.err records "stage bin: error:
field 'wall_shear' absent from source for 38 of 256 bins", after which the
driver reported "stage bin returned 1, halting before stage smooth".

The cause is upstream rather than in the pipeline configuration. Run 1842
terminated early — it was out-of-memory killed and wrote no checkpoint past step
1000 — so the wall_shear field was never written for the later part of the
domain. The partial output at outputs/stage2_bin.csv shows the gap directly: bin
3 onward has empty columns.

The pipeline cannot be repaired by changing its settings, because the input is
incomplete. It runs again once run 1842 has been rerun to completion and the
field set is whole; bin_count and smoothing_window stay as reviewed.
