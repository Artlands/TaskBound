help([[
openmpi 4.1.6 — site MPI build with UCX transport.
]])

whatis("Name: openmpi")
whatis("Version: 4.1.6")
whatis("Category: mpi, runtime")
whatis("Description: OpenMPI 4.1.6 with UCX 1.15, slurm PMIx integration")

local root = "/opt/site/openmpi/4.1.6"
prepend_path("PATH", pathJoin(root, "bin"))
prepend_path("LD_LIBRARY_PATH", pathJoin(root, "lib"))
setenv("OMPI_MCA_pml", "ucx")
setenv("OMPI_MCA_btl", "^openib")

-- Site notes
-- Per-rank buffer pools are allocated lazily; peak resident set size therefore
-- grows with mesh size rather than with rank count.
