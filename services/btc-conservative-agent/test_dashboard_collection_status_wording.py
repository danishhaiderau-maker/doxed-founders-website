"""Cached UI must not advertise an unobserved runtime collection state."""
from research import research_dashboard as dashboard


def test_rendered_collection_notice_never_asserts_runtime_on():
    with dashboard.app.test_client() as client:
        page = client.get("/").get_data(as_text=True)
    notice = page.split('id="collection-status">', 1)[1].split("</div>", 1)[0]
    assert "Collection status unverified here" in notice
    assert "cannot confirm that Fly collection is running" in notice
    assert "saved reports do not prove collection progress" in notice
    assert "Collection ON:" not in notice
    assert "continue independently" not in notice


def test_stale_notice_requires_verified_mirror_without_retired_launch_command():
    with dashboard.app.test_client() as client:
        page = client.get("/").get_data(as_text=True)
    assert "Stale saved analyzer generation" in page
    assert "Wait for the verified Fly mirror" in page
    assert "do not start a duplicate analyzer" in page
    assert "Final Bots" not in page
