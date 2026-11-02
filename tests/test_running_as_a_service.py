"""What changes when a service manager runs Muninn: a PATH of its own, and a log no one rotates."""

import os

from muninn.adapters.programs import find_program
from muninn.main import trim_log


def _program(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_a_program_off_the_service_path_is_found_where_programs_usually_are(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # launchd's
    brew = _program(tmp_path / "homebrew/bin", "tailscale-muninn-test")
    (tmp_path / "local/bin").mkdir(parents=True)
    (tmp_path / "local/bin/tailscale-muninn-test").write_text("not runnable")

    places = (str(tmp_path / "local/bin"), str(tmp_path / "homebrew/bin"))
    assert find_program("tailscale-muninn-test", places) == str(brew)
    assert find_program("not-installed-anywhere", places) is None


def test_the_path_comes_first(tmp_path, monkeypatch):
    on_path = _program(tmp_path / "path", "tool-muninn-test")
    _program(tmp_path / "usual", "tool-muninn-test")
    monkeypatch.setenv("PATH", str(tmp_path / "path"))

    assert find_program("tool-muninn-test", (str(tmp_path / "usual"),)) == str(on_path)


def test_a_log_past_its_limit_starts_again_with_the_last_one_kept_beside_it(tmp_path):
    log = tmp_path / "muninn.log"
    log.write_text("old\n" * 100)
    with open(log, "a") as held:  # the service manager's handle, appending
        trim_log(log, limit=50)
        held.write("after\n")
        held.flush()

    assert (tmp_path / "muninn.log.1").read_text() == "old\n" * 100
    assert log.read_text() == "after\n"  # written at the start again, not past a hole


def test_a_log_within_its_limit_or_missing_is_left_alone(tmp_path):
    log = tmp_path / "muninn.log"
    log.write_text("short\n")
    trim_log(log, limit=50)
    trim_log(tmp_path / "none.log", limit=50)

    assert log.read_text() == "short\n"
    assert not (tmp_path / "muninn.log.1").exists()
    assert not os.path.exists(tmp_path / "none.log")
