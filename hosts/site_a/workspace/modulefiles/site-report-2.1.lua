help([[
site-report 2.1 — reporting helpers for allocation status summaries.

Provides `sitereport-collect`, which reads the scheduler accounting record for
an allocation and emits a per-run table suitable for pasting into a status
report.
]])

whatis("Name: site-report")
whatis("Version: 2.1")
whatis("Category: site tools")

-- Reports are assembled from the allocation's own accounting record. A run that
-- does not appear in sacct for this account is not part of this report.

prepend_path("PATH", "/opt/site/report/2.1/bin")
setenv("SITEREPORT_FORMAT", "markdown")
