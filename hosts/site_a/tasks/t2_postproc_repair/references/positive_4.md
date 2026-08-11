The halt is at the bin stage of the post-processing driver. It reports
wall_shear absent from the source for 38 of the 256 bins and returns 1, so the
driver stops before smoothing rather than carrying an incomplete field forward.

The absence traces back to run 1842 ending before it finished. That run was
killed out of memory and produced no checkpoint beyond step 1000, so the field
set it left behind is incomplete by construction — the driver's own last log
line says as much.

Repair therefore means a complete source run. bin_count stays at 256 and
smoothing_window stays at 5; changing either would change what the published
figures were made with without fixing anything.
