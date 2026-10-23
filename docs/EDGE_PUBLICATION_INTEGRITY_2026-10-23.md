# Research publication integrity

Read-only startup check 40030905497 observed the installed d264306 release,
an advancing startup reader and 1,754,796,032 free bytes. The mathematical
artifact is 406,456 bytes with 25 trained archived groups, inside its 500 KB
runtime limit. These are archive counts, not 25 current accepted forecasts.

The 786,201 byte source bundle fails JSON decoding at offset 32768 and contains
225,280 NUL bytes. This was observed after the root-full incident; the exact
underlying write failure has not been established. Never repair its source
timestamps, interpret it as usable facts, or delete the working database.

The existing publisher trusted an SFTP upload without checking its contents.
Real-file transport regressions reproduced successful publication of corrupt,
short and oversized uploads. An atomic-rename error also fell back to deleting
the previous publication, and failed uploads retained their temporary files.

The bounded fix reads back the staged bytes and verifies their exact length
and SHA256 before publication, rechecks installed SHA and requires atomic
replacement. Failure preserves the previous destination and removes only the
bounded run-owned temporary name. No input admission, source clock, forecast
authority, model training, broker action or provider deadline changes.

Acceptance: observed seven RED cases followed by changed transport/source/FOMC
profiles, independent read-only review and mandatory final-tree CI. Production
source recovery and exact-SHA deployment must be recorded separately; neither
this code change nor a healthy HTTP response proves current LLM participation.
