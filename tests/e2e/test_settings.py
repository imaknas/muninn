"""The settings page says what this installation does without, and why: on the phone, in each language."""

from __future__ import annotations

import pytest

from e2e.screen import go
from e2e_support import Muninn, _page, _stop, expect_console

pytestmark = pytest.mark.e2e

OFF = [{"feature": "行事曆", "why": "沒有設定 Google client(secrets/google_client.json)"},
       {"feature": "每週回顧", "why": "設定關掉了(reflect: false)"}]


@pytest.fixture
def without_some(tmp_path, browser):
    m = Muninn(tmp_path, settings={"off": OFF})
    yield m
    _stop(m, browser)


def test_what_is_not_set_up_is_listed_with_why(browser, without_some):
    ctx, page, errors = _page(browser, without_some, **browser[0].devices["iPhone 14"])
    go(page, without_some, "settings", "沒開的功能")
    part = page.locator("section[aria-label='沒開的功能']")
    assert "2 項" in part.inner_text()
    for o in OFF:
        row = part.locator(".rule", has_text=o["feature"])
        assert o["why"] in row.inner_text()
    ctx.close()
    assert errors == [], errors


def test_nothing_is_listed_when_everything_is_set_up(page, muninn):
    go(page, muninn, "settings", "設定")
    assert page.locator("section[aria-label='沒開的功能']").count() == 0


def test_folders_to_read_are_chosen_on_the_page_and_saved(page, muninn, tmp_path):
    (tmp_path / "workspace").mkdir(exist_ok=True)  # one of the usual folders, found at home
    mine = tmp_path / "elsewhere" / "code"
    mine.mkdir(parents=True)
    go(page, muninn, "settings", "還沒選要讀的資料夾")
    card = page.locator("section[aria-label='讀取的資料夾']")
    card.get_by_role("button", name="加入").first.click()  # ~/workspace, found
    card.get_by_role("textbox", name="專案資料夾(git 活動)").fill(str(mine))
    card.locator(".tracker", has_text="專案資料夾").get_by_role("button", name="加入").last.click()
    card.get_by_role("button", name="儲存並重新啟動").click()
    card.get_by_text("已儲存").wait_for()
    assert muninn.setup.sources()["workspace_roots"] == ["~/workspace", str(mine)]


def test_a_google_client_is_set_up_by_following_the_steps_and_choosing_its_file(page, muninn, tmp_path):
    import json

    go(page, muninn, "settings", "連結 Google")
    card = page.locator("section[aria-label='連結 Google(郵件和行事曆)']")
    callback = f"{muninn.url}/oauth/google/callback"
    assert card.get_by_role("link", name="Gmail API").get_attribute("href").endswith("gmail.googleapis.com")
    downloaded = tmp_path / "client_secret_x.json"
    downloaded.write_text(json.dumps({"web": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s",
                                              "redirect_uris": ["https://elsewhere.example/cb"]}}), encoding="utf-8")
    card.get_by_label("選 Google 給的 JSON 檔").set_input_files(str(downloaded))
    expect_console(page, "400")  # the client is refused, on purpose
    card.get_by_role("button", name="使用這個用戶端").click()
    card.get_by_text(callback).wait_for()  # what to add in Google Cloud, said where the file was chosen
    downloaded.write_text(json.dumps({"web": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s",
                                              "redirect_uris": [callback]}}), encoding="utf-8")
    card.get_by_label("選 Google 給的 JSON 檔").set_input_files(str(downloaded))
    card.get_by_role("button", name="使用這個用戶端").click()
    card.get_by_text("已儲存").wait_for()
    assert (muninn.setup._cfg.secrets_dir / "google_client.json").is_file()


def test_form_filling_is_added_with_one_tap(page, muninn):
    go(page, muninn, "settings", "還沒加上填表功能")
    card = page.locator("section[aria-label='自動填求職表單']")
    card.get_by_role("button", name="加上填表功能").click()
    card.get_by_text("已儲存").wait_for()  # installed; this Muninn is started again by hand
