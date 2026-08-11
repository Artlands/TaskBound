The solver built cleanly from the site modules. openmpi/4.1.6 and hdf5/1.14.3
were the toolchain, cmake configure passed, the link succeeded, and the binary
is at build/bin/cfd-solver.

Getting the rerun moving is limited by capacity rather than by the build: the
compute partition shows 0 idle nodes, so the queued job 1856 will wait for a
slot rather than start now.
