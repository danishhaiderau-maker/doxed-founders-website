import pytest
import data_sync_bundle_client as mod
from test_data_sync_bundle_client import fixture


@pytest.mark.parametrize('phase', ['DESCRIPTOR', 'CHUNK'])
@pytest.mark.parametrize('failure', [503, 'TIMEOUT', 'CONNECTION_ERROR'])
def test_retry_context_is_bounded_and_secret_free(tmp_path, phase, failure):
    entry, gen, rows, good, calls = fixture(tmp_path)
    attempts = []
    def fetch(url, *, timeout):
        if phase == 'CHUNK' and 'descriptor=1' in url:
            return good(url, timeout=timeout)
        attempts.append(url)
        if failure == 'TIMEOUT':
            raise TimeoutError('SECRET_TOKEN')
        if failure == 'CONNECTION_ERROR':
            raise ConnectionError('SECRET_TOKEN')
        return failure, {'Secret': 'SECRET_TOKEN'}, b'SECRET_TOKEN'
    with pytest.raises(mod.BundleClientError, match='PACKAGE_RETRY_EXHAUSTED') as caught:
        mod.fetch_verified_package(entry, gen, rows, tmp_path/'stage', fetch, sleep=lambda _: None)
    d = caught.value.diagnostic
    assert d == dict(generation_id=gen['inventory_generation_id'], package_sha256=entry['package_sha256'],
                    phase=phase, offset=None if phase == 'DESCRIPTOR' else 0,
                    attempts=3, http_status=503 if failure == 503 else None,
                    transport_error=None if failure == 503 else failure)
    assert len(attempts) == 3
    assert 'SECRET_TOKEN' not in str(d)
