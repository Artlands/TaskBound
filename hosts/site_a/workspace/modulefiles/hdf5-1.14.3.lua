help([[
hdf5 1.14.3 — hierarchical data format library, built against openmpi/4.1.6.
]])

whatis("Name: hdf5")
whatis("Version: 1.14.3")
whatis("Category: io, library")
whatis("Description: parallel HDF5 for solver checkpoint and result output")

local root = "/opt/site/hdf5/1.14.3"
prepend_path("PATH", pathJoin(root, "bin"))
prepend_path("LD_LIBRARY_PATH", pathJoin(root, "lib"))
setenv("HDF5_ROOT", root)
setenv("HDF5_USE_FILE_LOCKING", "FALSE")

-- Site notes
-- Collective metadata writes are enabled by default in this build.
-- Checkpoint files are written to the run directory unless HDF5_OUTDIR is set.
