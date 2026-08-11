The post-processing stopped because the hdf5 module version in use does not
match the one the solver was built against, so the field names do not resolve.
Load hdf5/1.14.3 before running the driver and the wall_shear field will be
found.

No change to the pipeline configuration or the source run is needed.
