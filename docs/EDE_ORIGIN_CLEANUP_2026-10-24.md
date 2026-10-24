# EDE origin cleanup dispatch

## Observed failure

Installed cbe12ff passed production acceptance40037110023. Subsequent EDE
audits40037840190 and40067989442 failed after live replication at origin cleanup:
`NameError: name '__file__' is not defined`. New backup publication, actual
offhost inventory, EDE training and Active Edge handoff did not complete.

The uploaded origin controller establishes its file-relative import path.
The run wrapper executes that file, but cleanup executed its source through
`python3 -c`, where `__file__` does not exist. Existing transport tests mocked
the entire cleanup command and therefore missed Python startup.

## Bounded correction

Cleanup executes the same run-owned uploaded control file as the run wrapper.
Arguments retain shell quoting. PID/birth/executable checks, the existing
seven-second cleanup budget, matching native /proc guard, exit receipt and
refusal to publish without confirmed origin exit are unchanged. Database,
source clocks, service and risk/source admission are not modified.

The new regression captures the actual emitted command and uploaded bytes,
then runs that command in a local directory containing spaces. Before the
correction it reproduced the production NameError. On a foreign local /proc
it must reach and retain the existing namespace refusal; native GitHub CI
must run cleanup successfully and observe its exit receipt. Existing native
tests separately verify owned-reader termination and unrelated-PID survival.

Owning profile:52 passed,3 skipped in2.54s. The skips are pinned-copier
integration without its binary and native process tests in the foreign-/proc
local environment; final CI must exercise its existing native checks.
One scoped review, mandatory final-tree CI and actual exact-SHA delivery/
cleanup/export evidence remain required. No 18GB export was run to reproduce
this interpreter-dispatch error.
