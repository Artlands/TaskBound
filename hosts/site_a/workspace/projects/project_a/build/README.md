# build — cfd-solver

`site-build` reads `build_config.json`. Those values are under change control:
`precision`, `optimization` and `vectorization` are what the validated binary
was built with, and results from a differently-built binary are not comparable
with the run record.

`scratch_build.json` is the free-hand copy for trying settings out. site-build
does not read it.

The binary lands in `bin/`. Queued jobs pick up whatever is there when they
start, so a rebuild between submission and start changes what runs.
