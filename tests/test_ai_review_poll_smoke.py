def test_release_smoke_polls_one_admitted_review_and_keeps_request_budget(monkeypatch):
    from scripts import production_functional_smoke as smoke
    calls = []
    url = '/api/ai/reviews/' + 'a'*32
    replies = [(202, {'status': 'pending', 'review_job': {'poll_url': url}}, 5.),
               (202, {'status': 'pending', 'review_job': {'poll_url': url}}, 3.),
               (200, {'ok': True, 'verdict': 'complete'}, 7.)]
    def request(path, *, method='GET', timeout=0):
        calls.append((path, method))
        return replies.pop(0)
    monkeypatch.setattr(smoke, 'request', request)
    monkeypatch.setattr(smoke.time, 'sleep', lambda _: None)
    code, body, elapsed = smoke.request_ai_verdict()
    assert code == 200 and body['verdict'] == 'complete'
    assert elapsed == 7.
    assert calls == [('/api/ai/verdict', 'POST'), (url, 'GET'), (url, 'GET')]
