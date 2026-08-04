Job 1842 completed normally. The accounting record shows it finished after
25 minutes and the solver wrote its final output at step 1000, which is the
configured stopping point for this case.

There is nothing to fix. The reporter is most likely looking at the wrong job
id — the run they are waiting on is 1856, which is still queued.
