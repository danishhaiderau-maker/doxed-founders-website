import json
from unittest.mock import patch

from test_dashboard_download_freshness import dashboard


def test_actual_route_rejects_retired_before_any_expensive_work(tmp_path):
    (tmp_path / 'canonical_generation_retired.json').write_text(json.dumps({
        'generation_current': False, 'status': '<script>bad</script>'}))
    with patch.object(dashboard, 'DATA_ROOT', tmp_path), \
         patch.object(dashboard, '_generation_freshness_meta', side_effect=AssertionError('scan')), \
         patch.object(dashboard, '_ensure_current_gpt_audit_bundle', side_effect=AssertionError('zip')):
        client = dashboard.app.test_client()
        response = client.get('/download/everything', headers={'Accept': 'application/json'})
        assert response.status_code == 409
        assert response.json['status'] == 'MIRROR_RETIRED_AWAITING_VERIFIED_PROMOTION'
        html = client.get('/download/everything', headers={'Accept': 'text/html'})
        assert html.status_code == 409
        assert b'<script>' not in html.data
        assert html.headers['Cache-Control'] == 'no-store'


def test_actual_route_unbound_and_oversized_metadata_are_unavailable(tmp_path):
    with patch.object(dashboard, 'DATA_ROOT', tmp_path), \
         patch.object(dashboard, '_generation_freshness_meta', side_effect=AssertionError('scan')):
        assert dashboard.app.test_client().get('/download/everything').status_code == 503
        (tmp_path / 'canonical_dataset_current.json').write_text(' ' * 65537)
        assert dashboard.app.test_client().get('/download/everything').status_code == 503


def test_bound_identity_admits_existing_route_validation(tmp_path):
    identity = {key: 'fixture' for key in ('entry_hash', 'dataset_epoch',
        'source_revision', 'deployed_revision', 'tile_config_signature')}
    (tmp_path / 'canonical_dataset_current.json').write_text(json.dumps(identity))
    # Successful admission must continue through the original route, not
    # manufacture a ZIP or replace its existing completeness validation.
    with patch.object(dashboard, 'DATA_ROOT', tmp_path), \
         patch.object(dashboard, '_generation_freshness_meta', side_effect=RuntimeError('original-validation')):
        with dashboard.app.test_request_context('/download/everything'):
            import pytest
            with pytest.raises(RuntimeError, match='original-validation'):
                dashboard.download_everything()
