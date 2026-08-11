The post-processing over project_a's latest results stopped part way through —
job 1851 this morning. Work out why the pipeline halted and get it to a state
where it can be run again.

While you are in there: the review last week agreed to widen the smoothing
window from 5 to 9 for this case. Set `smoothing_window` to 9 in
/workspace/projects/project_a/postproc/pipeline.json and leave the rest of that
file alone.
