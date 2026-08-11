The failure is a permissions problem: the driver could not write into
postproc/outputs/ and so left a truncated file behind. The stage error about a
missing field is a downstream symptom of the incomplete write.

Ask the storage team to fix the group permissions on the outputs directory and
rerun the pipeline.
