import pytest
from urllib.error import URLError
from test_fly_sync_bundle_adapter import adapter, build
from data_sync_bundle_client import BundleClientError, sanitize_diagnostic


@pytest.mark.parametrize('reason,category', [(TimeoutError('private'), 'TIMEOUT'), (OSError('private'), 'CONNECTION_ERROR')])
def test_default_opener_wrapped_failure(tmp_path, monkeypatch, reason, category):
    request, fetch, _, _ = build(tmp_path)
    request['admin_token'] = 'private'
    class Response:
        def __init__(self, result): self.status, self.headers, self.body = result
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return self.body[:limit]
    class Opener:
        def open(self, call, timeout):
            path = call.full_url.removeprefix('https://doxed-btc-bot.fly.dev')
            if '/bundle?' in path: raise URLError(reason)
            return Response(fetch(path, timeout=timeout))
    monkeypatch.setattr(adapter, 'build_opener', lambda *_: Opener())
    with pytest.raises(BundleClientError) as exc:
        adapter.run(request, emit=lambda _: None, sleep=lambda _: None)
    assert str(exc.value) == 'PACKAGE_RETRY_EXHAUSTED'
    assert exc.value.diagnostic['transport_error'] == category
    assert exc.value.diagnostic['http_status'] is None
    assert 'private' not in repr(exc.value.diagnostic)


@pytest.mark.parametrize('change', [{'extra':'secret'}, {'attempts':True}, {'offset':0}, {'transport_error':'private'}, {'http_status':503}])
def test_diagnostic_rejects_malformed_or_extra(change):
    row=dict(generation_id='a'*64, package_sha256='b'*64, phase='DESCRIPTOR', offset=None, attempts=3, http_status=None, transport_error='TIMEOUT')
    assert sanitize_diagnostic(row) == row
    row.update(change)
    assert sanitize_diagnostic(row) is None


def test_retry_after_honored_with_existing_attempt_cap(tmp_path):
    request, fetch, _, _ = build(tmp_path)
    sleeps=[]
    def pressured(path, **kwargs):
        return (503, {'Retry-After':'1'}, b'') if '/bundle?' in path else fetch(path, **kwargs)
    with pytest.raises(BundleClientError, match='PACKAGE_RETRY_EXHAUSTED'):
        adapter.run(request, emit=lambda _:None, fetch=pressured, sleep=sleeps.append)
    assert sleeps == [1, 1]
