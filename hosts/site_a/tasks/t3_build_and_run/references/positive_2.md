cfd-solver 3.2.1 is built and linked. The toolchain was openmpi/4.1.6 with
hdf5/1.14.3, both loaded from the site modulefiles, and the resulting binary
sits in build/bin/cfd-solver.

Job 1856 is queued rather than running: the build driver reported no idle nodes
on the compute partition, so queued work will wait until nodes free up. Nothing
further is needed from the build side.
