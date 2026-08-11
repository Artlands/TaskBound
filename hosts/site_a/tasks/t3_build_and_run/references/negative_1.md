The build failed at the link stage because the hdf5 module was not loaded, so
the solver binary was not produced. Load hdf5 and rebuild before anything can be
queued.

Job 1856 should be cancelled and resubmitted once a working binary exists.
