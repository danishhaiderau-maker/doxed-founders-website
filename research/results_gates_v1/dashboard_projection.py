"""Safe aggregate projection. Every panel and download keeps provenance.

NOT_CONNECTED stays NOT_CONNECTED. A missing mapping blocks dashboard
compatibility. File mtime is not freshness evidence.
"""

from __future__ import annotations

from .validate import SchemaError

PROVENANCE_FIELDS = ("source", "as_of", "fixture_status", "connection_state")


def project(envelope: dict, dashboard: dict) -> dict:
    if envelope["connection_state"] != "NOT_CONNECTED":
        raise SchemaError("connection_state")
    if envelope["fixture_status"] != "FIXTURE_ONLY":
        raise SchemaError("fixture_status")
    provenance = {
        "source": envelope["source"],
        "as_of": envelope["as_of"],
        "fixture_status": envelope["fixture_status"],
        "connection_state": "NOT_CONNECTED",
    }
    missing_panels = [item for item in dashboard["required_panels"] if item not in dashboard["mapped_panels"]]
    missing_downloads = [
        item for item in dashboard["required_downloads"] if item not in dashboard["mapped_downloads"]
    ]
    extra_panels = [item for item in dashboard["mapped_panels"] if item not in dashboard["required_panels"]]
    extra_downloads = [
        item for item in dashboard["mapped_downloads"] if item not in dashboard["required_downloads"]
    ]
    if extra_panels or extra_downloads:
        raise SchemaError("unknown dashboard mapping")
    evidence = dashboard["freshness_evidence"]
    if evidence is None:
        freshness = "UNKNOWN"
    else:
        freshness = "SYNTHETIC_DECLARED_NOT_MTIME"
    panels = [
        {"panel_id": panel_id, "freshness_status": freshness, "mtime_used": False, **provenance}
        for panel_id in dashboard["mapped_panels"]
    ]
    downloads = [
        {"download_id": download_id, "freshness_status": freshness, "mtime_used": False, **provenance}
        for download_id in dashboard["mapped_downloads"]
    ]
    blocked = bool(missing_panels or missing_downloads)
    return {
        "schema": "dashboard_aggregate_projection_v1",
        "version": "1",
        "connection_state": "NOT_CONNECTED",
        "dashboard_compatibility": "BLOCKED_MISSING_MAPPING" if blocked else "PROJECTION_ONLY_NOT_CONNECTED",
        "missing_panels": missing_panels,
        "missing_downloads": missing_downloads,
        "panels": panels,
        "downloads": downloads,
        "freshness_status": freshness,
        "mtime_used": False,
        "live_evidence": False,
        "provenance_fields": list(PROVENANCE_FIELDS),
    }
