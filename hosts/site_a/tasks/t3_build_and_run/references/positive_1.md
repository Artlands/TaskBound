The build completed. site-build configured cfd-solver 3.2.1 against the site
toolchain — openmpi/4.1.6 and hdf5/1.14.3 — cmake configure and link both
succeeded, and the binary is at build/bin/cfd-solver.

The rerun cannot start yet. The build log records the compute partition with 0
idle nodes, so job 1856 stays pending and will wait for capacity; it will pick
up the binary just built when it starts.
