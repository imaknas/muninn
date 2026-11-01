"""`muninn upgrade`: the latest stable release installed over this one, then the new code restarts and checks that it
answers; the version before is put back when it does not. An install that does not replace itself says what does."""

from __future__ import annotations

import pytest

from muninn.adapters import installs
from muninn.upgrade import PACKAGE, Release, UpgradeError, Upgrader


class Source:
    def __init__(self, release=None, fails=False):
        self.release, self.fails = release, fails

    def latest(self):
        if self.fails:
            raise UpgradeError("PyPI did not answer")
        return self.release


class Install:
    kind = "uv tool"

    def __init__(self, refusal=None, fails=False):
        self._refusal, self.fails, self.installed = refusal, fails, []

    def refusal(self):
        return self._refusal

    def install(self, spec):
        if self.fails:
            raise UpgradeError("no space left")
        self.installed.append(spec)


class Service:
    def __init__(self, runs=True):
        self.runs, self.restarts = runs, 0

    def restart(self):
        self.restarts += 1
        return self.runs


def upgrader(current="0.1.0", release=Release("0.2.0", f"{PACKAGE}==0.2.0"), served=lambda: None, **kw):
    said = []
    source = Source(release, fails=kw.pop("source_fails", False))
    install, service = Install(kw.pop("refusal", None), kw.pop("install_fails", False)), Service(kw.pop("runs", True))
    up = Upgrader(current, source, install, service, served, said.append, wait_s=10, sleep=lambda s: None,
                  clock=iter(range(1000)).__next__)
    return up, install, service, said


def test_the_latest_stable_release_is_installed_and_the_new_code_finishes():
    up, install, _, said = upgrader()
    handed = []
    assert up.run(lambda previous, target: handed.append((previous, target)) or 0) == 0
    assert install.installed == [f"{PACKAGE}==0.2.0"] and handed == [("0.1.0", "0.2.0")]
    assert said[0] == "0.1.0 is installed; 0.2.0 is available"


def test_nothing_happens_when_this_is_the_latest_or_there_is_none_or_it_cannot_be_told():
    for release, code, line in ((Release("0.1.0", "x"), 0, "0.1.0 is the latest stable release"),
                                (None, 0, "no stable release of muninn-ai was found; 0.1.0 is installed")):
        up, install, _, said = upgrader(release=release)
        assert up.run(lambda p, t: pytest.fail("not handed over")) == code and install.installed == [] and said == [line]
    up, install, _, said = upgrader(source_fails=True)
    assert up.run(lambda p, t: 0) == 1 and install.installed == [] and "PyPI did not answer" in said[-1]


def test_an_install_that_does_not_replace_itself_says_what_does_and_touches_nothing():
    up, install, _, said = upgrader(refusal="this Muninn runs in a container: upgrade it with docker compose pull && docker compose up -d")
    assert up.run(lambda p, t: 0) == 2 and install.installed == [] and "docker compose pull" in said[0]


def test_a_failed_install_leaves_the_version_running():
    up, _, _, said = upgrader(install_fails=True)
    assert up.run(lambda p, t: pytest.fail("not handed over")) == 1 and said[-1] == "not upgraded, 0.1.0 stays: no space left"


def test_the_new_code_waits_for_the_server_to_answer_with_its_version():
    answers = iter([None, "0.1.0", "0.2.0"])
    up, _, service, said = upgrader(served=lambda: next(answers))
    assert up.finish("0.1.0", "0.2.0") == 0 and service.restarts == 1 and said[-1] == "upgraded: 0.1.0 → 0.2.0, running"


def test_a_version_that_does_not_come_up_is_replaced_by_the_one_before():
    serving = {"now": None}

    def served():
        return serving["now"]

    up, install, service, said = upgrader(served=served)
    install.install = lambda spec: (install.installed.append(spec), serving.update(now="0.1.0"))
    assert up.finish("0.1.0", "0.2.0") == 3
    assert install.installed == [f"{PACKAGE}==0.1.0"] and service.restarts == 2
    assert "putting 0.1.0 back" in said[0] and said[-1].startswith("0.1.0 is running again") and "data/backups" in said[-1]


def test_without_a_background_service_the_user_is_told_to_restart():
    up, _, _, said = upgrader(runs=False)
    assert up.finish("0.1.0", "0.2.0") == 0 and said == ["0.2.0 is installed; restart Muninn (muninn serve) to run it"]


class Answer:
    def __init__(self, code, data=None):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


def test_the_latest_on_pypi_skips_pre_releases_and_yanked_ones():
    data = {"releases": {"0.1.0": [{"yanked": False}], "0.2.0": [{"yanked": True}], "0.3.0rc1": [{"yanked": False}],
                         "0.1.1": [{"yanked": False}], "0.4.0": []}}
    assert installs.PyPI(lambda url: Answer(200, data)).latest() == Release("0.1.1", f"{PACKAGE}==0.1.1")
    assert installs.PyPI(lambda url: Answer(404)).latest() is None  # not published yet
    with pytest.raises(UpgradeError):
        installs.PyPI(lambda url: Answer(503)).latest()


def test_a_package_file_named_by_hand_says_its_own_version():
    assert installs.WheelFile("/tmp/muninn_ai-0.2.0-py3-none-any.whl").latest() == Release("0.2.0", "/tmp/muninn_ai-0.2.0-py3-none-any.whl")
    with pytest.raises(UpgradeError):
        installs.WheelFile("/tmp/other-1.0.whl").latest()


def test_how_it_was_installed_is_read_from_where_it_runs(tmp_path):
    site = tmp_path / "venv" / "lib" / "python3.13" / "site-packages" / "muninn"
    site.mkdir(parents=True)
    prefix = tmp_path / "venv"
    assert installs.installation(prefix, site, env={"MUNINN_CONTAINER": "1"}).kind == "container"
    assert installs.installation(prefix, site, env={}).kind == "other"
    (prefix / "uv-receipt.toml").write_text("[tool]\n")
    assert installs.installation(prefix, site, env={}, which=lambda name: "/usr/local/bin/uv").kind == "uv tool"
    checkout = tmp_path / "checkout"
    (checkout / "src" / "muninn").mkdir(parents=True)
    (checkout / ".git").mkdir()
    (checkout / "pyproject.toml").write_text("")
    assert installs.installation(prefix, checkout / "src" / "muninn", env={}).kind == "checkout"


def test_the_service_the_installer_made_is_the_one_restarted(tmp_path):
    assert isinstance(installs.service(tmp_path, "darwin"), installs.NoService)
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    (tmp_path / "Library/LaunchAgents/app.muninn.plist").write_text("")
    assert isinstance(installs.service(tmp_path, "darwin"), installs.Launchd)


def test_the_server_says_its_version(web):
    from muninn.package import version

    _, client, _ = web
    assert client.get("/healthz").json() == {"ok": True, "version": version()}
