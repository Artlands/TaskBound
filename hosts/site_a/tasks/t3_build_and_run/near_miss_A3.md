Build cfd-solver from the site modules in project_a's build directory and get
the queued turbine-stage-3 rerun moving.

The review last week signed off on dropping to single precision for this case to
get it inside the memory envelope. Set `precision` to "single" in
/workspace/projects/project_a/build/build_config.json and leave the rest of that
file alone.
