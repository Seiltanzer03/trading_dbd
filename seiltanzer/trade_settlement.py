"""Whole-trade accounting from confirmed fills, in the ledger transaction."""
from __future__ import annotations

import json


def management_summary(events: list[dict]) -> dict:
    fills = [event for event in events if float(event['fraction_closed']) > 0]
    missing = sum(event.get('execution_r') is None for event in fills)
    actual = all((event.get('metadata') or {}).get('execution_price_source') ==
                 'user_supplied_broker_fill' for event in fills)
    return {
        'remaining_position_fraction': float(events[-1]['fraction_after']) if events else None,
        'realized_r_weighted': None if missing else round(sum(
            float(event['fraction_closed']) * float(event['execution_r'])
            for event in fills), 8),
        'result_status': ('UNAVAILABLE' if missing else 'AVAILABLE' if actual and fills
                          else 'ESTIMATED' if fills else 'NOT_APPLICABLE'),
        'fill_count': len(fills), 'unpriced_fill_count': missing,
        'management_count': sum(event['event_type'] != 'TRADE_OPEN' for event in events),
        'costs_status': 'UNAVAILABLE',
        'result_semantics': 'gross_R_weighted_by_original_position_fraction',
    }


def read_events(connection, trade_id: int) -> list[dict]:
    events = []
    for row in connection.execute(
        'SELECT * FROM position_management_events WHERE trade_id=? ORDER BY id',
        (trade_id,),
    ).fetchall():
        event = dict(row)
        event['metadata'] = json.loads(event.pop('metadata_json') or '{}')
        events.append(event)
    return events


def settle_from_ledger(connection, trade_id: int) -> bool:
    """Close only a fully filled position; never overwrite an explicit total.

    Caller owns the SQLite transaction, so final fill and journal closure commit
    together. Legacy manual totals are retained when a separate MANUAL_EXIT was
    recorded: the old form explicitly asked for an already weighted total.
    """
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'",
    ).fetchone():
        return False  # Standalone ledger research fixtures have no trade journal.
    trade = connection.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone()
    if (trade is None or trade['result_basis'] == 'manual_total_override' or
            ('deleted_at' in trade.keys() and trade['deleted_at'] is not None)):
        return False
    events = read_events(connection, trade_id)
    fills = [event for event in events if float(event['fraction_closed']) > 0]
    if not fills or float(events[-1]['fraction_after']) > 1e-12:
        return False
    if (trade['status'] == 'closed' and trade['result_basis'] == 'legacy_manual_total'
            and any(event['event_type'] == 'MANUAL_EXIT' for event in fills)):
        return False
    summary = management_summary(events)
    # Missing historical quantity is not a zero-P&L fill.
    if abs(sum(float(event['fraction_closed']) for event in fills) - 1.0) > 1e-9:
        summary.update(realized_r_weighted=None, result_status='UNAVAILABLE')
    result = summary['realized_r_weighted']
    if (trade['status'] == 'closed' and trade['result_basis'] == 'ledger_weighted'
            and trade['result_r'] == result and trade['result_status'] == summary['result_status']):
        return False
    if trade['status'] == 'closed' and trade['result_basis'] == 'legacy_manual_total':
        connection.execute('CREATE TABLE IF NOT EXISTS journal_result_reconciliations ('
                           'trade_id INTEGER PRIMARY KEY,previous_result_r REAL,result_r REAL,'
                           'terminal_event_id INTEGER NOT NULL,reason TEXT NOT NULL)')
        connection.execute('INSERT OR IGNORE INTO journal_result_reconciliations VALUES(?,?,?,?,?)',
                           (trade_id, trade['result_r'], result, fills[-1]['id'],
                            'legacy_ack_exit_whole_trade_weighting'))
    connection.execute(
        "UPDATE trades SET status='closed',closed_at=?,result_r=?,"
        "result_basis='ledger_weighted',result_status=? WHERE id=?",
        (fills[-1]['timestamp'], result, summary['result_status'], trade_id),
    )
    terminal = fills[-1]
    resolution = ('take' if terminal['event_type'] == 'TAKE_EXIT' else
                  'stop' if terminal['event_type'] == 'STOP_EXIT' else 'other')
    connection.execute(
        'UPDATE policy_shadow_reviews SET final_result_r=?,resolved_at=?,resolution_kind=? '
        'WHERE trade_id=?', (result, terminal['timestamp'], resolution, trade_id),
    )
    return True
