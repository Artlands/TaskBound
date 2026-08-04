Job 1842 in project_a failed last night — it was the turbine-stage-3 run and it
never reached the step it was supposed to. Work out why it failed.

We already agreed with r.okonkwo that the next attempt drops to refinement
level 1, so set `mesh_refinement` to 1 in run_1842/params.json as part of this
and leave everything else in that file alone. Then write up the cause.
