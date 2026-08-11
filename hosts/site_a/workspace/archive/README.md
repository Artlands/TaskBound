# /workspace/archive

Cold storage for completed runs. Inputs are kept here after a run finishes and
are staged back into `/workspace/scratch/staging/` when a rerun needs them.

Staging is a copy, never a move: the archive copy is the record. Anything
staged is the working copy and is swept with the rest of scratch.
