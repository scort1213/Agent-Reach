import errno
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_reach.collection.weread_helper import file_lock


def _acquire_in_child(path, waiting, acquired, crash=False):
    waiting.set()
    with file_lock.exclusive_lock(path):
        acquired.set()
        if crash:
            os._exit(7)


def _stop(process):
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("lock subprocess did not finish")


def test_lock_serializes_processes_and_releases_on_exception(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    waiting, acquired = ctx.Event(), ctx.Event()
    path = tmp_path / "task.lock"
    child = ctx.Process(target=_acquire_in_child, args=(path, waiting, acquired))
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            with file_lock.exclusive_lock(path):
                child.start()
                assert waiting.wait(10), "child did not reach lock"
                assert not acquired.wait(0.3), "second process entered locked task"
                raise RuntimeError("simulated task failure")
        assert acquired.wait(10), "lock not released after task failure"
    finally:
        _stop(child)
    assert child.exitcode == 0
    assert path.exists(), "do not unlink a potentially contended lock file"


def test_process_crash_releases_lock(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    path = tmp_path / "task.lock"
    waiting, acquired = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_acquire_in_child, args=(path, waiting, acquired, True))
    child.start()
    assert acquired.wait(10)
    _stop(child)
    assert child.exitcode == 7
    acquired.clear()
    successor = ctx.Process(target=_acquire_in_child, args=(path, waiting, acquired))
    successor.start()
    try:
        assert acquired.wait(10), "dead process left task locked"
    finally:
        _stop(successor)
    assert successor.exitcode == 0


def test_windows_contention_retries_but_other_errors_propagate(tmp_path, monkeypatch):
    attempts, sleeps = [], []

    def locking(fd, mode, count):
        attempts.append((fd, mode, count))
        if len(attempts) < 3:
            raise OSError(errno.EACCES, "already locked")

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(locking=locking, LK_NBLCK=2))
    monkeypatch.setattr(file_lock.time, "sleep", sleeps.append)
    with (tmp_path / "task.lock").open("a+b") as handle:
        file_lock._windows_acquire(handle)
        assert len(attempts) == 3 and sleeps == [0.1, 0.1]

        def broken(*args):
            raise OSError(errno.EBADF, "invalid descriptor")

        monkeypatch.setattr(sys.modules["msvcrt"], "locking", broken)
        with pytest.raises(OSError, match="invalid descriptor"):
            file_lock._windows_acquire(handle)


def test_weread_helper_runs_as_standalone_folder_without_main_package(tmp_path):
    source = Path(file_lock.__file__).parent
    helper = tmp_path / "standalone-helper"
    shutil.copytree(source, helper, ignore=shutil.ignore_patterns("__pycache__"))
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    # Explicitly prevent imports from the main package; sibling imports remain
    # available just as they are when executing the standalone script normally.
    code = (
        "import runpy, sys; "
        "sys.modules['agent_reach'] = None; "
        "sys.path.insert(0, sys.argv[1]); "
        "runpy.run_path(sys.argv[1] + '/run.py', run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(helper)],
        input=json.dumps({"home": str(tmp_path / "fresh-session"), "account": "fixture"}),
        capture_output=True, text=True, cwd=tmp_path, env=env, timeout=15, check=True,
    )
    assert json.loads(result.stdout)["status"] == "login_required", result.stderr


def test_podcast_import_does_not_require_unix_fcntl():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.modules['fcntl'] = None; "
         "from agent_reach.collection import podcast; print('portable import OK')"],
        capture_output=True, text=True, timeout=15, check=True,
    )
    assert result.stdout.strip() == "portable import OK"


def _get_in_child(directory, ready, begin, calls):
    from agent_reach.collection import get_client

    class Client:
        def request(self, endpoint, payload=None, *, create=False):
            if create:
                with calls.get_lock():
                    calls.value += 1
                time.sleep(0.2)
                return {"note_id": "12345"}, {}
            return {"note": {"audio": {"original": "fixture transcript " * 20}}}, {}

    get_client.Client = Client
    ready.set()
    assert begin.wait(10)
    # English fixtures intentionally receive a quality warning; they still
    # exercise creation, saving, and resuming without real network requests.
    assert get_client.main([directory, "12345678901", "https://v.douyin.com/fixture/"]) == 2


def _douyin_in_child(directory, ready, begin, calls):
    from agent_reach.collection import jobs

    def get_subprocess(args, **kwargs):
        with calls.get_lock():
            calls.value += 1
        time.sleep(0.2)
        root, label = Path(args[-3]), args[-2]
        original = root / "original.txt"
        original.write_text("fixture transcript", encoding="utf-8")
        jobs.save(root / (label + ".json"), {
            "status": "original_returned",
            "fields": {"audio.original": {"file": str(original), "sha256": jobs.digest(original)}},
        })
        return SimpleNamespace(returncode=0)

    jobs.subprocess.run = get_subprocess
    ready.set()
    assert begin.wait(10)
    result = jobs.collect({
        "mode": "account", "query": "fixture", "captured_at": "2026-09-10T15:00:00+08:00",
        "items": [{"video_id": "12345678901"}],
    }, Path(directory), limit=1)
    assert result["items"][0]["status"] == "needs_browser_media"


@pytest.mark.parametrize("worker", [_get_in_child, _douyin_in_child])
def test_competing_tasks_submit_get_once(tmp_path, worker):
    ctx = multiprocessing.get_context("spawn")
    begin, ready_one, ready_two = ctx.Event(), ctx.Event(), ctx.Event()
    calls = ctx.Value("i", 0)
    children = [ctx.Process(target=worker, args=(str(tmp_path), ready, begin, calls))
                for ready in (ready_one, ready_two)]
    for child in children:
        child.start()
    try:
        assert ready_one.wait(10) and ready_two.wait(10)
        begin.set()
    finally:
        for child in children:
            _stop(child)
    assert [child.exitcode for child in children] == [0, 0]
    assert calls.value == 1, "concurrent resumption submitted the same task twice"


def test_parent_collection_lock_does_not_block_get_subprocess(tmp_path):
    code = (
        "import pathlib, sys; "
        "from agent_reach.collection.weread_helper.file_lock import exclusive_lock; "
        "\nwith exclusive_lock(pathlib.Path(sys.argv[1]) / 'get.lock'): print('acquired')"
    )
    with file_lock.exclusive_lock(tmp_path / "collection.lock"):
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                                capture_output=True, text=True, timeout=10, check=True)
    assert result.stdout.strip() == "acquired"
