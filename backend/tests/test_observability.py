import asyncio
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import delete, select

from reelvault.backup import create_backup, restore_backup
from reelvault.config import Settings
from reelvault.db import make_engine, make_sessionmaker
from reelvault.jobs.manager import JobManager
from reelvault.locations import register_root
from reelvault.main import create_app
from reelvault.maintenance import purge_expired_trash
from reelvault.media.ffmpeg import Canceled
from reelvault.migrate import alembic_config, upgrade
from reelvault.models import AuditEvent, Job, JobMetric, Video, utcnow
from reelvault.observability import audit, record_job

from .conftest import HEADERS, login, upload_ready

TOKEN = "metrics-fixture-secret-01234567890123456789"


def samples_from(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert response.headers["cache-control"] == "no-store"
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    }


def value(samples, name, **labels):
    return samples[(name, tuple(sorted(labels.items())))]


def test_login_audit_records_results_and_real_peer_without_secrets(client, settings):
    with client.app.state.sessionmaker() as db:
        db.execute(delete(AuditEvent))
        db.commit()
    secret = "DO-NOT-LOG-PASSWORD"
    response = client.post(
        "/api/auth/login",
        headers={"X-Forwarded-For": "spoofed-peer"},
        json={"username": "unknown", "password": secret, "code": "654321"},
    )
    assert response.status_code == 401
    event = client.get("/api/system/audit").json()["items"][0]
    assert event["event"] == "login_failure" and event["actor"] == "unknown"
    assert event["peer"] == "testclient" and event["details"] == {"reason": "credentials"}
    assert secret not in json.dumps(event) and "654321" not in json.dumps(event)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/system/audit").status_code == 401
    login(client)
    assert client.get("/api/system/audit").json()["items"][0]["event"] == "login_success"
    for _ in range(settings.login_max_failures + 1):
        client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert client.get("/api/system/audit?event=login_limited").json()["total"] == 1
    assert client.get("/api/system/audit?event=unknown").status_code == 422
    assert client.get("/api/system/audit?limit=101").status_code == 422


def test_delete_restore_purge_and_failed_purge_have_transactional_audits(
    client, settings, samples, monkeypatch
):
    video = upload_ready(client, samples["a"])
    with client.app.state.sessionmaker() as db:
        path = settings.data_dir / db.get(Video, video["id"]).file_path
    assert client.delete(f"/api/videos/{video['id']}").status_code == 200
    assert client.post(f"/api/videos/{video['id']}/restore").status_code == 200
    assert (
        client.post(
            "/api/videos/batch", json={"ids": [video["id"]], "action": "delete"}
        ).status_code
        == 200
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            "reelvault.api.videos._purge", lambda *args: (_ for _ in ()).throw(OSError("fixture"))
        )
        with pytest.raises(OSError):
            client.delete(f"/api/videos/{video['id']}?permanent=true")
    assert path.exists()
    assert client.get("/api/system/audit?event=video_purge").json()["total"] == 0
    assert client.post("/api/trash/empty").json()["deleted"] == 1
    rows = client.get("/api/system/audit").json()["items"]
    actions = [row["event"] for row in reversed(rows) if row["target"] == video["id"]]
    assert actions == ["video_trash", "video_restore", "video_trash", "video_purge"]
    assert all(row["actor"] == "admin" for row in rows)
    assert not path.exists()


def test_audit_cursor_retention_cap_and_automatic_trash(client, settings, samples):
    settings.audit_max_events = 100
    with client.app.state.sessionmaker() as db:
        db.execute(delete(AuditEvent))
        db.add(
            AuditEvent(
                event="login_failure",
                actor="old",
                peer="",
                details={},
                created_at=utcnow() - timedelta(days=100),
            )
        )
        db.commit()
        for index in range(107):
            audit(db, settings, "login_failure", actor=f"user-{index}")
        db.commit()
    first = client.get("/api/system/audit?limit=17").json()
    assert first["total"] == 100 and first["items"][0]["actor"] == "user-106"
    ids = [row["id"] for row in first["items"]]
    cursor = first["next_before"]
    while cursor:
        page = client.get(f"/api/system/audit?limit=17&before={cursor}").json()
        ids.extend(row["id"] for row in page["items"])
        cursor = page["next_before"]
    assert len(ids) == len(set(ids)) == 100 and ids == sorted(ids, reverse=True)
    video = upload_ready(client, samples["a"])

    with client.app.state.sessionmaker() as db:
        db.get(Video, video["id"]).deleted_at = utcnow() - timedelta(days=100)
        db.commit()
    assert purge_expired_trash(settings, client.app.state.sessionmaker) == 1
    event = client.get("/api/system/audit?event=trash_retention").json()["items"][0]
    assert event["actor"] == "system" and event["target"] == video["id"]


def test_metrics_auth_histograms_queue_offline_storage_and_job_deletion(client, settings, tmp_path):
    assert client.get("/api/system/metrics").status_code == 404
    settings.metrics_token = TOKEN
    assert client.get("/api/system/metrics").status_code == 401
    assert client.get(f"/api/system/metrics?token={TOKEN}").status_code == 401
    assert (
        client.get("/api/system/metrics", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    now = utcnow()
    with client.app.state.sessionmaker() as db:
        jobs = [
            Job(
                kind="edit",
                status="succeeded",
                started_at=now - timedelta(seconds=12.5),
                finished_at=now,
            ),
            Job(
                kind="edit", status="failed", started_at=now - timedelta(seconds=3), finished_at=now
            ),
            Job(kind="hls", status="canceled", finished_at=now),
            Job(
                kind="link_import",
                status="paused",
                params={"storage_bytes": 1024, "storage_plan": {"local": 1024}},
            ),
        ]
        db.add_all(jobs)
        db.flush()
        for job in jobs[:3]:
            record_job(db, job)
            record_job(db, job)
        db.commit()
    root = tmp_path / "archive"
    root.mkdir()
    location, entry = register_root(settings, root, "archive")
    settings.storage_locations = {location: entry}
    root.rename(tmp_path / "offline")
    response = client.get("/api/system/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    parsed = samples_from(response)
    assert value(parsed, "reelvault_jobs", kind="link_import", status="paused") == 1
    assert value(parsed, "reelvault_jobs_completed_total", kind="edit", status="succeeded") == 1
    assert (
        value(parsed, "reelvault_job_duration_seconds_sum", kind="edit", status="succeeded") == 12.5
    )
    assert (
        value(
            parsed, "reelvault_job_duration_seconds_bucket", kind="edit", status="succeeded", le="5"
        )
        == 0
    )
    assert (
        value(
            parsed,
            "reelvault_job_duration_seconds_bucket",
            kind="edit",
            status="succeeded",
            le="15",
        )
        == 1
    )
    assert (
        value(
            parsed,
            "reelvault_job_duration_seconds_bucket",
            kind="edit",
            status="succeeded",
            le="+Inf",
        )
        == 1
    )
    assert value(parsed, "reelvault_job_duration_seconds_count", kind="hls", status="canceled") == 0
    assert value(parsed, "reelvault_jobs_completed_total", kind="hls", status="canceled") == 1
    assert value(parsed, "reelvault_storage_online", location=location) == 0
    assert value(parsed, "reelvault_storage_reserved_bytes", location="local") == 1024
    assert TOKEN not in response.text and str(root) not in response.text
    with client.app.state.sessionmaker() as db:
        db.execute(delete(Job).where(Job.status.in_(["succeeded", "failed", "canceled"])))
        db.commit()
    after = samples_from(
        client.get("/api/system/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    )
    assert value(after, "reelvault_jobs", kind="edit", status="succeeded") == 0
    assert value(after, "reelvault_jobs_completed_total", kind="edit", status="succeeded") == 1


def test_concurrent_job_metrics_are_exactly_once_and_survive_backup(settings, tmp_path):
    settings.metrics_token = TOKEN
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    sessions = make_sessionmaker(engine)
    now = utcnow()
    with sessions() as db:
        jobs = [
            Job(
                kind="edit",
                status="succeeded",
                started_at=now - timedelta(seconds=10),
                finished_at=now,
            )
            for _ in range(40)
        ]
        db.add_all(jobs)
        db.commit()
        ids = [job.id for job in jobs]

    def record(job_id):
        with sessions() as db:
            record_job(db, db.get(Job, job_id))
            db.commit()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, ids + ids))
    with sessions() as db:
        row = db.get(JobMetric, ("edit", "succeeded"))
        assert row.completed == row.duration_count == 40 and row.duration_sum == 400
        assert row.buckets[0] == 0 and row.buckets[-1] == 40
        audit(db, settings, "login_success", actor="fixture")
        db.commit()
    engine.dispose()
    archive = create_backup(settings, tmp_path / "backup.zip")
    with zipfile.ZipFile(archive) as zip_file:
        assert TOKEN.encode() not in zip_file.read("config.json")
    target = Settings(data_dir=tmp_path / "restored", update_check=False)
    restore_backup(archive, target)
    with TestClient(create_app(target), headers=HEADERS) as restored:
        with restored.app.state.sessionmaker() as db:
            assert db.get(JobMetric, ("edit", "succeeded")).completed == 40
            assert db.scalar(select(AuditEvent.actor)) == "fixture"
        assert restored.get("/api/system/metrics").status_code == 404


def test_upgrade_restart_confirmation_and_old_schema_migration(settings):
    from alembic import command

    from reelvault import __version__

    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    sessions = make_sessionmaker(engine)
    with sessions() as db:
        audit(
            db,
            settings,
            "upgrade_applied",
            actor="admin",
            peer="operator",
            details={"to_version": __version__},
        )
        db.commit()
    engine.dispose()
    for _ in range(2):
        with (
            TestClient(create_app(settings), headers=HEADERS) as client,
            client.app.state.sessionmaker() as db,
        ):
            rows = list(
                db.scalars(select(AuditEvent).where(AuditEvent.event == "upgrade_restarted"))
            )
            assert len(rows) == 1 and rows[0].actor == "admin"
    engine = make_engine(settings.db_path)
    with engine.begin() as connection:
        config = alembic_config(str(engine.url))
        config.attributes["connection"] = connection
        command.downgrade(config, "0027")
    upgrade(engine)
    with make_sessionmaker(engine)() as db:
        assert not db.scalar(select(AuditEvent.id))
    engine.dispose()


def test_worker_success_failure_running_and_queued_cancellation_and_restart_metrics(settings):
    settings.ensure_dirs()
    engine = make_engine(settings.db_path)
    upgrade(engine)
    sessions = make_sessionmaker(engine)

    async def handler(ctx, job):
        if job.params.get("failure"):
            raise RuntimeError("fixture failure")
        if job.params.get("canceled"):
            raise Canceled()

    manager = JobManager(settings, sessions, {"ingest": handler})
    with sessions() as db:
        jobs = [
            Job(kind="ingest", params=params)
            for params in ({}, {"failure": True}, {"canceled": True})
        ]
        dormant = Job(kind="ingest", status="paused")
        interrupted = Job(
            kind="ingest", status="paused", started_at=utcnow() - timedelta(seconds=10)
        )
        db.add_all([*jobs, dormant, interrupted])
        db.commit()
        manager.cancel(db, dormant)

    async def run():
        await manager.start()
        try:
            await manager.wait_idle()
        finally:
            await manager.stop()

    asyncio.run(run())
    with sessions() as db:
        assert db.get(JobMetric, ("ingest", "succeeded")).completed == 1
        assert db.get(JobMetric, ("ingest", "failed")).completed == 2
        assert db.get(JobMetric, ("ingest", "canceled")).completed == 2
        assert db.get(JobMetric, ("ingest", "canceled")).duration_count == 1
        assert all(job.metrics_recorded for job in db.scalars(select(Job)))
    engine.dispose()


@pytest.mark.parametrize("failed", [False, True])
def test_upgrade_api_persists_actor_and_outcome_audit(client, monkeypatch, failed):
    from reelvault.updates import ReleaseInfo

    updater = client.app.state.updater
    restarted = []
    monkeypatch.setattr(updater, "auto_upgrade_blocker", lambda: None)
    monkeypatch.setattr(updater, "request_restart", lambda: restarted.append(True))

    async def check():
        updater.state.latest = ReleaseInfo(
            version="0.99.0",
            tag="v0.99.0",
            name="fixture",
            url="url",
            notes="",
            published_at=None,
            prerelease=False,
        )
        return updater.status()

    def install(release):
        if failed:
            raise RuntimeError("DO-NOT-AUDIT-INSTALLER-SECRET")

    monkeypatch.setattr(updater, "check", check)
    monkeypatch.setattr(updater, "_upgrade_sync", install)
    assert client.post("/api/system/update/apply").status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if updater.state.phase == "failed" or restarted:
            break
        time.sleep(0.01)
    assert updater.state.phase == ("failed" if failed else "restarting")
    if not failed:
        assert restarted == [True]
    history = client.get("/api/system/audit").json()
    records = [row for row in reversed(history["items"]) if row["event"].startswith("upgrade_")]
    assert [row["event"] for row in records] == [
        "upgrade_started",
        "upgrade_failed" if failed else "upgrade_applied",
    ]
    assert all(row["actor"] == "admin" and row["peer"] == "testclient" for row in records)
    assert "DO-NOT-AUDIT-INSTALLER-SECRET" not in json.dumps(history)
