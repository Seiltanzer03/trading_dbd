# Bounded Yandex closed-log archive — 2026-10-14

Accepted runtime remains fdffdbde030164bf4cd00fba24ef8f6d0d133121.
Inventory38581000670 completed successfully. EDE38585087620 stopped before
the18.6GB snapshot transfer because production lacked the unchanged1GiB WAL
growth reserve. No audit/publish/profit result is asserted for that stopped run.

Read-only inventory38601167665 measured912400384 available bytes on a30GB
volume and an18.69GB authoritative DB. The EDE ledger is already archived in
the existing Yandex bucket:395082163 original bytes,43067286 compressed local
bytes, full-GET-verified receipt. Re-archiving it cannot recover more space.

This ops-only package archives closed system journals and old closed runner
diagnostics, plus btmp.1 after a bounded standard rename/create rotation. It excludes active journals,
application data, databases, WALs, symlinks and unknown paths. Each source is
bounded to80MiB and the selected batch to400MiB. The off-host worker copies
and checks the original, compresses there, uploads to the existing private
Yandex Object Storage using GitHub configuration, and performs full GET/
decompression/SHA checks before requesting exact unchanged closed-source
retirement. Receipts preserve original paths for restoration. No source data
is staged on the VPS. Historical system diagnostics will be retrieved from
their cloud archive; trading journal/history and service behavior are untouched.

The package is invoked through a one-off branch of the existing diagnostic
workflow; it adds no provider, server or scheduler, and changes no main/runtime
code or admission gates. At this checkpoint no archive has been applied.
The optional btmp rotation retains the same old inode and every byte, creates
a fresh root/utmp active file preserving its original0600/0660 mode, and refuses any pre-existing rotated file.
It does not truncate a source or signal/restart services. Existing canonical
Git metadata compaction may run after the archives if more capacity is needed;
it validates unchanged HEAD/refs/tracked files and preserves all canonical roots.
Safety tests:38 passed,6 subtests in1.88s, including open/changed files, wrong
receipts, symlinks, database paths and cloud full-GET failure. Tests were added
after the new ops helper; no RED-first claim is made. One final review is
completed before the concrete archive operation. It found two Important issues:
the constructed remote namespace lacked the archive helper alias, and receipt
write_text could follow a sidecar symlink or overwrite a prior file. One fix pass
binds the exact helper functions and writes a hash-addressed0600 receipt using
exclusive/no-follow creation and file/directory fsync. Existing receipts fail
closed without modification. Three new tests first failed, including actual
constructed-payload NameError; the same payload now creates the receipt and
retires only the unchanged closed fixture. A further regression ensures the
standard0660 btmp mode is preserved as well as0600, rather than changing its
permissions. Final owning profile:42 passed,6 subtests in1.80s.
Full runtime CI is not repeated.

Hosted run38605871707 stopped at pre-operation tests; no live archive, rotation
or metadata compaction ran. Its nonroot runner exposed three fixture ownership
assumptions, and an existing workflow test required PyYAML. The test fixture now
models root metadata only for its temporary btmp paths, with a separate nonroot
rejection regression; the production ownership guard is unchanged. The workflow
installs its missing test dependency before any operation.

If actual recovered space cannot meet the existing reserve, further heavy
research remains blocked; neither repeat training nor a smaller guard substitutes
for real capacity. Protected backups and active runner components are preserved.
