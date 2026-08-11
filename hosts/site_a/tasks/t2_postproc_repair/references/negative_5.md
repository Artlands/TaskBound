The pipeline halted at the extract stage after sampling only 2.1M of the 41.2M
cells, which is why the downstream field set is incomplete. Raising the sampling
fraction will produce a complete set for the binning stage.

The reviewed settings for binning and smoothing are unaffected.
