The post-processing halted because the bin count is too high for this mesh. At
256 bins the later bins have too few cells to populate, which is why the run
reported empty output from bin 3 onward.

Set bin_count to 64 in pipeline.json and the stage will complete. The source run
is fine; this is a configuration problem in the post-processing.
