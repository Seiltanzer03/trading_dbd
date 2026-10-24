# Offhost EDE inventory preserves its immutable input

Production EDE40082288165 on81bae completed the consistent live export. Native
origin exit was confirmed at10:21:18UTC, verified18437914624-byte snapshot export
completed10:23:59UTC, and Yandex backup verification completed10:41:13UTC. The
actual offhost inventory and exact-chain inventory marker also completed.
Research preparation then refused `Mutable SQLite sidecar is present`; discovery,
publication and Active Edge handoff did not run. This is not an eight-family or
LLM readiness receipt.

The offhost snapshot retains a WAL-mode main-file header. Opening it with only
`mode=ro` during inventory can create `-wal`/`-shm` even without a write query.
The following preparation correctly rejects these files. Reproduced with real
SQLite using the actual workflow inventory shell: unchanged database bytes but
new sidecars and a failed immutable-input contract.

The workflow now explicitly selects `--verified-immutable-input` for its already
exported and verified offhost snapshot. That reader checks the existing regular,
single-link, sidecar-free identity contract and uses `mode=ro&immutable=1`. Its
report identifies this open mode. It does not delete unknown sidecars, rewrite
headers, bypass preparation verification, or perform another database copy.
Canonical live production input refuses immutable mode. Default inventory still
uses an owned consistent read transaction, sees committed WAL rows, and retains
its maintenance reader lock through SQLite close.

Behavioral coverage executes the actual workflow shell against a read-only
WAL-header snapshot and then calls real research preparation. It also checks
sidecar refusal, unchanged source bytes, committed live WAL visibility and
production-mode refusal/actual flock release. Earlier RED: actual workflow
created sidecars; three explicit-mode admission cases lacked their new API.
Independent review also reproduced a CLI import failure without PYTHONPATH.
The script now uses the existing file-relative repo bootstrap; its actual
workflow regression removes PYTHONPATH rather than injecting it.
Final owning profile85passed5.56s. Mandatory final CI and server release remain
separate requirements. No action policy, evidence gate, expert weight, source
clock or execution acknowledgement is changed.
