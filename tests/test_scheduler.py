"""Tests for src/scheduler/scheduler.py

Covers: job overlap skip, weekend skip, retry after failure, no double-retry.
"""

import os
import sys
from unittest.mock import MagicMock, patch

# Ensure settings can import in test environment
os.environ.setdefault("BOT_TOKEN", "test_token_for_scheduler_tests")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """Reset module-level state between tests."""
    import src.scheduler.scheduler as mod

    old = mod._scheduler
    mod._scheduler = None
    # Release lock if held from a previous test
    try:
        mod._job_lock.release()
    except RuntimeError:
        pass
    yield
    if mod._scheduler is not None and mod._scheduler is not old:
        try:
            mod._scheduler.shutdown(wait=False)
        except Exception:
            pass
    mod._scheduler = None


# ── 1. Job overlap → skip ────────────────────────────────────

def test_overlap_skip():
    """When lock is held, run_screening_job returns False (overlap)."""
    import src.scheduler.scheduler as mod

    mod._job_lock.acquire()
    try:
        with patch.object(mod, "is_market_day", return_value=True):
            result = mod.run_screening_job()
        assert result is False
    finally:
        mod._job_lock.release()


# ── 2. Weekend → skip ───────────────────────────────────────

def test_weekend_skip():
    """On non-market day, run_screening_job returns False without screening."""
    import src.scheduler.scheduler as mod

    with patch.object(mod, "is_market_day", return_value=False):
        result = mod.run_screening_job(is_retry=False)
    assert result is False


# ── 3. Failure → retry scheduled ─────────────────────────────

def test_retry_on_failure():
    """Screening failure schedules a retry job, returns False."""
    import src.scheduler.scheduler as mod

    mock_sched = MagicMock()
    mod._scheduler = mock_sched

    mock_merger = MagicMock()
    mock_merger.get_all_merged = MagicMock(side_effect=RuntimeError("network error"))
    mock_scorer = MagicMock()

    with patch.object(mod, "is_market_day", return_value=True), \
         patch.object(mod, "_save_status"), \
         patch.dict(sys.modules, {
             "src.data": MagicMock(),
             "src.data.merger": mock_merger,
             "src.analysis": MagicMock(),
             "src.analysis.scorer": mock_scorer,
         }):
        result = mod.run_screening_job(is_retry=False)

    assert result is False
    mock_sched.add_job.assert_called_once()
    call_kw = mock_sched.add_job.call_args[1]
    assert call_kw["kwargs"]["is_retry"] is True
    assert call_kw["id"] == "screening_retry"
    assert call_kw["trigger"] == "date"


# ── 4. Retry failure → no double-retry ──────────────────────

def test_no_double_retry():
    """When is_retry=True and it fails again, no second retry is scheduled."""
    import src.scheduler.scheduler as mod

    mock_sched = MagicMock()
    mod._scheduler = mock_sched

    mock_merger = MagicMock()
    mock_merger.get_all_merged = MagicMock(side_effect=RuntimeError("still broken"))
    mock_scorer = MagicMock()

    with patch.object(mod, "is_market_day", return_value=True), \
         patch.object(mod, "_save_status"), \
         patch.dict(sys.modules, {
             "src.data": MagicMock(),
             "src.data.merger": mock_merger,
             "src.analysis": MagicMock(),
             "src.analysis.scorer": mock_scorer,
         }):
        result = mod.run_screening_job(is_retry=True)

    assert result is False
    mock_sched.add_job.assert_not_called()


# ── 5. start_scheduler creates 3 cron jobs ──────────────────

def test_start_scheduler_creates_jobs():
    """Mode weekly (default): 1 job Sabtu; harga tutup Jumat dipakai
    sepanjang minggu (keputusan user 2026-07-04, hemat free-tier VM)."""
    import src.scheduler.scheduler as mod

    sched = mod.start_scheduler()
    try:
        jobs = sched.get_jobs()
        job_ids = {j.id for j in jobs}
        assert "screening_mingguan" in job_ids
        assert len(jobs) == 1
        # Job Sabtu wajib membawa ignore_market_day (Sabtu bukan hari bursa)
        assert jobs[0].kwargs.get("ignore_market_day") is True
    finally:
        sched.shutdown(wait=False)
        mod._scheduler = None


# ── 6. Idempotent start ─────────────────────────────────────

def test_start_scheduler_idempotent():
    """Calling start_scheduler twice returns the same instance."""
    import src.scheduler.scheduler as mod

    s1 = mod.start_scheduler()
    s2 = mod.start_scheduler()
    assert s1 is s2
    s1.shutdown(wait=False)
    mod._scheduler = None
