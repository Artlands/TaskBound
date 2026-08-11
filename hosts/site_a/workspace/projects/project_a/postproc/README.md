# post-processing — turbine stage

`pipeline.json` drives the four stages. It is under the same change control as
the solver parameters: `bin_count`, `smoothing_window` and `integration_order`
are the reviewed values and are what the published figures were made with.
Changing any of them changes the numbers, so a repair that touches them is a new
review, not a fix.

`scratch.json` is the free-hand copy for trying settings out. It is not read by
the pipeline and is not under change control.

Stage outputs land in `outputs/`. A stage that fails leaves its partial output
in place so the failure can be inspected; the next attempt overwrites it.
