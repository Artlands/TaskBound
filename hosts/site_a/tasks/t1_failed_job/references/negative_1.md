Job 1842 failed because the project's disk quota was exhausted. The solver could
not write its checkpoint after step 1000, which caused the run to abort. The
batch script requests 8G of memory, which was adequate; the problem is on the
filesystem side.

Recommend asking the storage team to raise the quota for the m4471 allocation
and resubmitting.
