"""HM 25 Oct 06:30: /api/restart-cause records who initiated a restart (deploy / config_change / manual / crash),
and any non-deploy restart is an alert + AMBER fly.restart_initiator health check."""
import runtime_uptime as ru

T0 = 1792800000.0
REL = {"FLY_RELEASE_VERSION": "720", "FLY_MACHINE_VERSION": "m1", "FLY_IMAGE_REF": "reg/app:dep-a"}


def _boot(tmp_path, now, rev, rel):
    t = ru.UptimeTracker(tmp_path / ru.STATE_FILE)
    t.boot(now, rev, release=rel)
    return t


def test_first_boot_no_alert(tmp_path):
    rc = _boot(tmp_path, T0, "aaa", REL).restart_cause(T0 + 1)
    assert rc["initiator"] == "first_boot" and rc["initiator_alert"] is False and rc["release"] == REL


def test_rev_change_is_deploy(tmp_path):
    _boot(tmp_path, T0, "aaa", REL)
    rc = _boot(tmp_path, T0 + 100, "bbb", dict(REL, FLY_RELEASE_VERSION="721")).restart_cause(T0 + 101)
    assert rc["initiator"] == "deploy" and not rc["initiator_alert"] and "initiator=deploy" in rc["restart_cause"]
    assert rc["prev_release"]["FLY_RELEASE_VERSION"] == "720"


def test_same_rev_new_image_is_deploy(tmp_path):
    _boot(tmp_path, T0, "aaa", REL)
    rc = _boot(tmp_path, T0 + 100, "aaa", dict(REL, FLY_IMAGE_REF="reg/app:dep-b")).restart_cause()
    assert rc["initiator"] == "deploy"


def test_same_rev_new_release_is_config_change_alert(tmp_path):
    _boot(tmp_path, T0, "aaa", REL)
    rc = _boot(tmp_path, T0 + 100, "aaa", dict(REL, FLY_RELEASE_VERSION="721", FLY_MACHINE_VERSION="m2")).restart_cause()
    assert rc["initiator"] == "config_change" and rc["initiator_alert"] is True
    assert "secret/config" in rc["initiator_basis"]


def test_same_release_clean_shutdown_is_manual(tmp_path):
    t = _boot(tmp_path, T0, "aaa", REL)
    t.record_shutdown(T0 + 50, signal_name="SIGTERM")
    rc = _boot(tmp_path, T0 + 100, "aaa", REL).restart_cause()
    assert rc["initiator"] == "manual" and rc["initiator_alert"] is True


def test_same_release_no_marker_is_crash(tmp_path):
    _boot(tmp_path, T0, "aaa", REL)
    rc = _boot(tmp_path, T0 + 100, "aaa", REL).restart_cause()
    assert rc["initiator"] == "crash" and rc["initiator_alert"] is True


def test_release_identity_reads_only_listed_env_keys():
    env = {"FLY_RELEASE_VERSION": "9", "FLY_IMAGE_REF": "r:x", "SOME_TOKEN": "nope"}
    assert ru.release_identity(env) == {"FLY_RELEASE_VERSION": "9", "FLY_IMAGE_REF": "r:x"}


def test_health_check_amber_after_non_deploy_restart(tmp_path, monkeypatch):
    import bot
    _boot(tmp_path, T0, "aaa", REL)
    t = _boot(tmp_path, T0 + 100, "aaa", dict(REL, FLY_RELEASE_VERSION="721"))
    monkeypatch.setitem(bot._RUNTIME_UPTIME, "tracker", t)
    c = bot._restart_initiator_check(T0 + 200)
    assert c["id"] == "fly.restart_initiator" and c["status"] == "AMBER" and c["observed"].startswith("config_change")
    assert bot._restart_initiator_check(T0 + 100 + ru.INITIATOR_ALERT_WINDOW_SEC + 1)["status"] == "GREEN"
    assert any(x["id"] == "fly.restart_initiator" for x in bot._system_health_fly_self_checks(T0 + 200))
    d = _boot(tmp_path, T0 + 300, "bbb", REL)
    monkeypatch.setitem(bot._RUNTIME_UPTIME, "tracker", d)
    assert bot._restart_initiator_check(T0 + 301)["status"] == "GREEN"
