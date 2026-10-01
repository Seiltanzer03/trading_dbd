# Personal terminal completion review — 1 October 2026

Scope: code review against main fe8ebd0 plus the journal settlement correction;
read-only production /api/state and /api/journal. This is not a claim that every
instrument was live-tested or that the full manager has a demonstrated edge.

## Ready for manual use

- Stateful HOLD/CLOSE_10/25/50/EXIT and seven extended management contracts.
- Evidence, source-quality, hard-risk and paired net-MC selection gates.
- Actual remainder, repeated-reduction guard, fixed conditional order quantity.
- Explicit manual broker confirmation, idempotent execution and original risk.
- Deterministic fallback, bounded API, parallel Python/browser CI and exact-SHA deploy acceptance.
- With this correction: one final confirmation, automatic journal settlement,
  weighted P&L, fill/management history and explicit manual/ladder fill capture.

## Remaining work, ordered by practical value

1. **Actual costs and broker reconciliation.** Add measured commissions, swaps
   and fill corrections/whole-trade reconciliation. Price-only R is gross; the
   decision model's 0.01R fallback must not become measured journal costs.
   Old unrecorded partials require user/broker records, not inference from max_r.
2. **Multi-instrument quote acceptance.** NAS100 was live on OANDA in the
   inspected production snapshot. SP500/US30/UK100/JPY100 have configured OANDA
   quotes, GER40 has FPMARKETS, metals/FX use spot pairs. Configuration and unit
   tests do not substitute for simultaneous live comparisons with the user's
   broker during each market's session. Delayed options/proxy relevance remains
   a real limitation, especially for proxy-specific gamma/option-wall actions.
3. **Economic feedback on the frozen current manager.** Show paired net outcomes
   per independent trade, risk saved, upside sacrificed and action frequency.
   Existing old-rule evidence is only 4/9/2 independent cases. Inventory useful
   stored history and any available backups read-only, then replay eligible paths.
   Continue manual use while measuring; do not make this a prerequisite for a
   private working product or claim that all actions improve expectancy.
4. **Compact decision presentation.** Lead with one action, parameters, Expected
   gain, tail-risk gain and the reason for rejection; keep the full audit behind
   details. This improves daily usability without changing authority gates.

## Separate research, not product blockers

Prospective Q→P calibration, G.1S/G.1-M held-out validation and learned-policy
promotion can continue independently. Missing source data or statistically
unproven edge should be displayed honestly, not turned into endless implementation
requirements. No broker execution automation is required for this personal manual terminal.
