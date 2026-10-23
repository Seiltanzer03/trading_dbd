from seiltanzer.journal import Journal
from test_decision_research import _snapshot


def add_review(journal, trade, captured):
    journal.record_ai_verdict(trade['id'], _snapshot(trade['id'], captured), 'fixture', 'test')
    return journal._conn.execute(
        'SELECT review_id FROM decision_snapshots WHERE trade_id=? AND captured_ts=?',
        (trade['id'], captured)).fetchone()[0]


def test_live_point_cost_does_not_scan_accumulated_review_history(tmp_path):
    journal = Journal(str(tmp_path / 'path.db'))
    try:
        trade = journal.open_trade(3, 'NAS100', 'long', 100., 99., 102.)
        t0 = 1_700_000_000.
        reviews = [add_review(journal, trade, t0+i) for i in range(4)]
        with journal._conn:
            journal._conn.executemany(
                'INSERT INTO decision_path_points(review_id,ts,price,r) VALUES(?,?,?,?)',
                ((review, t0+10+i, 100., 0.) for review in reviews for i in range(20_000)))
        operations = 0

        def budget():
            nonlocal operations
            operations += 100
            return operations > 20_000

        journal._conn.set_progress_handler(budget, 100)
        try:
            assert journal.record_decision_market_point(
                trade['id'], ts=t0+30_000, price=101., r=1.) == 4
        finally:
            journal._conn.set_progress_handler(None, 0)
        assert operations <= 20_000
        assert journal._conn.execute(
            'SELECT COUNT(*) FROM decision_path_points WHERE ts=?', (t0+30_000,)).fetchone()[0] == 4
    finally:
        journal._conn.set_progress_handler(None, 0)
        journal.close()


def test_live_point_preserves_unresolved_cutoff_and_latest_point_semantics(tmp_path):
    journal = Journal(str(tmp_path / 'semantics.db'))
    try:
        other = journal.open_trade(3, 'NAS100', 'long', 100., 99., 102.)
        journal.close_trade(other['id'], 0.)
        trade = journal.open_trade(3, 'NAS100', 'long', 100., 99., 102.)
        now = 1_700_000_100.
        ids = {name: add_review(journal, other if name == 'other' else trade, now+offset)
               for name, offset in [('old', -90), ('recent', -80), ('empty', -70),
                                    ('future_point', -60), ('resolved', -50), ('future_review', 10), ('other', -40)]}
        with journal._conn:
            journal._conn.execute('DELETE FROM decision_path_points WHERE review_id=?', (ids['empty'],))
            journal._conn.executemany(
                'INSERT INTO decision_path_points(review_id,ts,price,r) VALUES(?,?,?,?)',
                [(ids['recent'], now-1, 100., 0.), (ids['future_point'], now+5, 100., 0.)])
            journal._conn.execute(
                'INSERT INTO decision_replays VALUES(?,?,?,?,?,?)',
                (ids['resolved'], trade['id'], now-1, 'fixture', 'test', '{}'))
        assert journal.record_decision_market_point(trade['id'], ts=now, price=101., r=1.) == 2
        inserted = journal._conn.execute(
            'SELECT review_id,price,r FROM decision_path_points WHERE ts=? ORDER BY review_id', (now,)).fetchall()
        assert [tuple(row) for row in inserted] == sorted([(ids['old'], 101., 1.), (ids['empty'], 101., 1.)])
        assert journal.record_decision_market_point(trade['id'], ts=now, price=101., r=1.) == 0
        # Equality at the existing two-second minimum admits the same reviews.
        assert journal.record_decision_market_point(trade['id'], ts=now+2, price=101.5, r=1.5) == 3
        assert journal._conn.execute(
            'SELECT COUNT(*) FROM decision_path_points WHERE review_id=? AND ts=?',
            (ids['future_point'], now+2)).fetchone()[0] == 0
    finally:
        journal.close()
