"""What the user does with Muninn, checked in a real browser, on the phone and the wide screen.
These say what happens, not where it is drawn: things are found by their names (screen.py), so a
redesign keeps them passing. What the current layout itself looks like is in test_layout.py."""

from __future__ import annotations

from datetime import timedelta

import pytest

from e2e.screen import (answer_with, button, chores, entry, eventually, fits_the_window, go, no_stray_text, open_panel, packet, panel, pill, place, say,
                        say_box, seed, take_back, talk, waiting)

pytestmark = pytest.mark.e2e
SCREENS = ["page", "desktop"]


def expect():
    return pytest.importorskip("playwright.sync_api").expect


def eventually_on(page, holds, seconds=15):
    """Waits until `holds()` holds where it depends on what the page does (a route's handler runs while the page waits)."""
    for _ in range(seconds * 20):
        if holds():
            return
        page.wait_for_timeout(50)
    raise AssertionError("never happened")


# ── what waits, and nothing broken anywhere ────────────────────────────────

@pytest.mark.parametrize("screen", SCREENS)
def test_what_waits_is_in_the_panel_and_never_in_the_conversation(request, muninn, screen):
    pg = request.getfixturevalue(screen)
    seed(muninn)
    go(pg, muninn)
    expect()(pill(pg)).to_have_accessible_name(pytest.importorskip("re").compile(r"^\d+ 件等你"))
    expect()(talk(pg).get_by_text("Red or white roses?")).to_have_count(0)  # the conversation is only the conversation
    open_panel(pg)
    for shown in ("Red or white roses?", "面試邀請"):
        expect()(waiting(pg, shown)).to_have_count(1)
    no_stray_text(pg)


def test_the_button_shows_what_waits_is_on_its_way_before_it_is_read(page, muninn):
    held = []
    page.route("**/api/waiting", lambda r: held.append(r))
    page.goto(f"{muninn.url}/")
    loading = page.get_by_role("button", name="讀取中")
    expect()(loading).to_be_visible()  # not an empty frame
    expect()(loading).to_have_attribute("aria-busy", "true")
    eventually_on(page, lambda: held)
    page.unroute("**/api/waiting")
    for r in held:
        r.continue_()
    expect()(pill(page)).to_be_visible()
    expect()(pill(page)).not_to_have_attribute("aria-busy", "true")


def test_a_button_whose_first_read_failed_says_so_and_a_tap_reads_again(page, muninn):
    from e2e_support import expect_console
    seed(muninn)
    expect_console(page, "status of 500")  # the failed read, on purpose
    page.route("**/api/waiting", lambda r: r.fulfill(status=500, body="{}"))
    page.goto(f"{muninn.url}/")
    failed = page.get_by_role("button", name="沒讀到等你的事;點一下再讀一次")
    expect()(failed).to_be_visible()  # not loading for ever
    page.unroute("**/api/waiting")
    failed.click()
    expect()(pill(page)).to_have_accessible_name(pytest.importorskip("re").compile(r"^\d+ 件等你"))
    expect()(waiting(page, "Red or white roses?")).to_have_count(1)


@pytest.mark.parametrize("screen", SCREENS)
def test_every_place_draws_with_no_stray_text_and_fits_the_window(request, muninn, screen):
    pg = request.getfixturevalue(screen)
    seed(muninn)
    chores(muninn, 12)
    p = packet(muninn)
    sizes = ({"width": 1440, "height": 800}, {"width": 1200, "height": 800}) if screen == "desktop" else (None,)
    for size in sizes:
        if size:
            pg.set_viewport_size(size)
        for where, shown in (("", None), ("things", "今天"), ("today", "做了"), ("progress", "黑色細線"), ("lists", "Garden chore 11"),
                             ("settings", "記一筆"), (f"packet/{p.id}", "Why us?")):
            go(pg, muninn, where, shown)
            no_stray_text(pg)
            assert fits_the_window(pg), (size, where)


# ── tasks and ideas: listed in 清單, handled by saying so ─────────────────

@pytest.mark.parametrize("screen", SCREENS)
def test_a_task_is_done_by_saying_so_and_taken_back_by_saying_so(request, muninn, screen):
    pg = request.getfixturevalue(screen)
    seed(muninn)
    go(pg, muninn)
    place(pg, "清單")
    entry(pg, "Order soil for the rose bed").click()  # the conversation is now about it
    answer_with(muninn, ("close_task", {"status": "done"}))
    say(pg, "這個做完了")
    eventually(lambda: muninn.h.engine.tasks.get("m1")["status"] == "done")  # (its title was on screen before the reply)
    assert '"kind": "task"' in muninn.h.local.calls[-1][1]
    take_back(pg, muninn)
    assert muninn.h.engine.tasks.get("m1")["status"] == "open"


def test_every_task_is_listed_none_folded_away(desktop, muninn):
    seed(muninn)
    chores(muninn, 12)
    go(desktop, muninn, "lists", "Garden chore 11")
    expect()(panel(desktop).get_by_role("region", name="任務").get_by_role("button")).to_have_count(13)


def test_looking_through_tasks_leaves_one_divider_not_one_per_task(desktop, muninn):
    seed(muninn)
    chores(muninn, 3)
    go(desktop, muninn)
    for name in ("Garden chore 0", "Garden chore 1", "Garden chore 2"):
        desktop.evaluate("location.hash = '#lists'")  # within the page: what was shown stays
        entry(desktop, name).click()
    expect()(talk(desktop).get_by_role("separator")).to_have_count(1)  # only where it ended up
    talk(desktop).get_by_text("現在在談:Garden chore 2").wait_for()


@pytest.mark.parametrize("screen", SCREENS)
def test_an_idea_is_talked_about_set_aside_and_taken_back(request, muninn, screen):
    pg = request.getfixturevalue(screen)
    seed(muninn)
    go(pg, muninn, "lists", "澆水排程")
    entry(pg, "幫社區花園做一個澆水排程的小工具").click()
    muninn.replies({"actions": [], "reply": "可以先從一個花圃試。"})
    say(pg, "從哪裡開始?")
    talk(pg).get_by_text("可以先從一個花圃試。").wait_for()
    routed = [prompt for _, prompt in muninn.h.local.calls if '"said"' in prompt]
    assert '"kind": "idea"' in routed[-1]  # the turn was about the idea
    answer_with(muninn, ("settle_idea", {"decision": "shelve"}))
    say(pg, "先擱著")
    eventually(lambda: muninn.h.engine.ideas.all()[0].get("status") == "shelved")
    take_back(pg, muninn)
    assert muninn.h.engine.ideas.all()[0].get("status", "open") == "open"


def test_the_panel_counts_tasks_and_ideas(desktop, muninn):
    seed(muninn)
    go(desktop, muninn)
    nav = open_panel(desktop).get_by_role("navigation", name="看更多")
    expect()(nav.get_by_role("link", name="清單 1 個任務 · 1 個想法")).to_have_count(1)


# ── what waits, answered ─────────────────────────────────────────────────

@pytest.mark.parametrize("screen", SCREENS)
def test_discussing_a_plan_item_ends_in_a_next_step_one_tap_carries_out(request, muninn, screen):
    pg = request.getfixturevalue(screen)
    item = muninn.h.engine.review_queue()[0]
    muninn.replies({"actions": [], "reply": "這是規劃時推的,你沒說過。",
                    "suggest": [{"label": "要這一項", "tool": "decide_item", "args": {"decision": "confirm"}}]})
    go(pg, muninn)
    open_panel(pg)
    waiting(pg, item["title"]).first.click()
    button(panel(pg), "討論", exact=True).click()  # the conversation is now about it
    say(pg, "這是哪來的?")
    t = talk(pg)
    t.get_by_text("這是規劃時推的").wait_for()
    button(t, "要這一項", exact=True).click()
    t.get_by_text("確認").last.wait_for()
    assert item["id"] not in [x["id"] for x in muninn.h.engine.review_queue()]


def test_what_waits_holds_no_reminders_and_a_mail_to_do_opens_its_mail(desktop, muninn):
    from muninn.core.models import InboxItem

    muninn.h.store.add_inbox(InboxItem(None, muninn.h.clock.now(), "health", "alert", "23:30 之後還在工作", "", {}, "anomaly:late-night"))
    due = muninn.h.clock.now().date().isoformat()
    muninn.h.engine.tasks.add("去交易所驗證帳戶", due, ["mail:personal:x1"], "交易所要求驗證帳戶")
    go(desktop, muninn)
    open_panel(desktop)
    waiting(desktop, "去交易所驗證帳戶").click()
    expect()(panel(desktop).get_by_role("link", name="看信")).to_have_attribute("href", "#mail/personal%3Ax1")
    expect()(waiting(desktop, "23:30 之後還在工作")).to_have_count(0)


def test_a_drafted_domain_is_taken_whole_and_given_back(desktop, muninn):
    waiting_items = [x for x in muninn.h.engine.review_queue() if x["domain"] == "jobs"]
    assert len(waiting_items) > 1
    go(desktop, muninn)
    open_panel(desktop)
    waiting(desktop, waiting_items[0]["title"]).first.click()
    panel(desktop).get_by_role("button", name=f"「Job search」整份照草稿({len(waiting_items)} 項)").click()
    expect()(waiting(desktop, waiting_items[0]["title"])).to_have_count(0)  # (gone from the screen before the server is done)
    eventually(lambda: not [x for x in muninn.h.engine.review_queue() if x["domain"] == "jobs"])  # all taken at once
    take_back(desktop, muninn)
    assert len([x for x in muninn.h.engine.review_queue() if x["domain"] == "jobs"]) == len(waiting_items)  # and given back at once


# ── what is open is what is talked about ──────────────────────────────────

@pytest.mark.parametrize("screen", SCREENS)
def test_what_is_open_is_what_the_input_talks_about(request, muninn, screen):
    pg = request.getfixturevalue(screen)
    p = packet(muninn)
    muninn.replies({"actions": [], "reply": "這家的基本條件你不符合。"})
    go(pg, muninn, f"packet/{p.id}", "Why us?")
    with pg.expect_request(lambda r: r.url.endswith("/api/command")) as sent:
        say(pg, "我條件都不符合,還要投嗎?")
    assert sent.value.post_data_json["about"] == {"kind": "packet", "id": p.id}
    talk(pg).get_by_text("這家的基本條件你不符合。").wait_for()


def test_once_the_panel_is_closed_what_it_showed_is_no_longer_what_is_talked_about(desktop, muninn):
    p = packet(muninn)
    muninn.replies({"actions": [], "reply": "好,接著談那個想法。"})
    go(desktop, muninn, f"packet/{p.id}", "Why us?")
    panel(desktop).get_by_role("link", name="收起").click()
    expect()(panel(desktop)).to_be_hidden()
    with desktop.expect_request(lambda r: r.url.endswith("/api/command")) as sent:
        say(desktop, "接續想法 1 的討論")
    assert sent.value.post_data_json["about"] is None  # not a new conversation about the packet


def test_a_packet_sent_is_marked_applied(desktop, muninn):
    p = packet(muninn)
    go(desktop, muninn)
    open_panel(desktop)
    waiting(desktop, "Acme Staff Engineer").click()
    desktop.get_by_role("button", name="送出了,記成已投遞").click()
    expect()(panel(desktop)).to_be_hidden()  # done: back to the conversation
    assert muninn.h.store.get_prepared(p.id).status == "done"


def test_a_packet_is_set_aside_by_saying_so_and_taken_back(desktop, muninn):
    p = packet(muninn)
    answer_with(muninn, ("dismiss_packet", {"why": "基本條件不符"}), reply="基本條件三項都不符,先不投。")
    go(desktop, muninn, f"packet/{p.id}", "Why us?")
    say(desktop, "這三項基本條件我都不符合,不投了")
    talk(desktop).get_by_text("先不投:Acme Staff Engineer").wait_for()
    assert muninn.h.store.get_prepared(p.id).status == "dismissed"
    take_back(desktop, muninn)
    assert muninn.h.store.get_prepared(p.id).status == "ready"


def test_a_filled_application_form_is_looked_over_and_sent_with_one_press(desktop, muninn):
    from muninn.core.apply import Field, Form, SiteResult, Sites

    class Site:
        name = "Greenhouse"
        sent = []

        def handles(self, url):
            return True

        def submit(self, form, values, files):
            self.sent.append(dict(values))
            return SiteResult(True, said="Thank you for applying")

    p = packet(muninn)
    muninn.h.engine.applications.sites = Sites([Site()])
    muninn.h.store.set_state(f"apply:{p.id}", {"state": "filled", "site": "Greenhouse", "files": {},
                                               "values": {"first_name": "Ada", "sponsor": "No"},
                                               "form": Form("Greenhouse", "https://jobs.example/acme", (
                                                   Field("first_name", "First Name", "text", True),
                                                   Field("sponsor", "Visa sponsorship?", "select", True, ("Yes", "No")))).to_dict()})
    go(desktop, muninn, f"packet/{p.id}", "申請表 · 在 Greenhouse 上填好")
    expect()(panel(desktop).get_by_text("Visa sponsorship?")).to_be_visible()  # each field with what it was filled with
    panel(desktop).get_by_role("button", name="送出申請").click()
    desktop.get_by_text("送出了:Acme Staff Engineer", exact=False).first.wait_for()
    assert Site.sent == [{"first_name": "Ada", "sponsor": "No"}]
    assert muninn.h.store.get_prepared(p.id).status == "done"


# ── today ──────────────────────────────────────────────────────────────────

def test_today_shows_the_calendar_and_what_muninn_planned_but_never_window_titles(desktop, muninn):
    from conftest import at

    from muninn.core.models import CalendarEvent, Observation, Sensitivity

    muninn.h.store.add_observations([Observation("windows", at(2026, 10, 22, 8), "window", "w1", {"duration_s": 2400, "title": "secret.txt"},
                                                 "jobs", None, Sensitivity.PERSONAL)])
    muninn.calendar.mine = [CalendarEvent("e1", at(2026, 10, 22, 9, 30), at(2026, 10, 22, 9, 45), "Standup")]
    muninn.calendar.blocks = [CalendarEvent("b1", at(2026, 10, 22, 14), at(2026, 10, 22, 15), "Muninn · Go practice")]
    go(desktop, muninn, "today", "Muninn 排的:Go practice")
    timeline = desktop.get_by_role("region", name="今天的時間軸")
    expect()(timeline.get_by_text("Standup")).to_be_visible()
    expect()(timeline.get_by_text("Job search").first).to_be_visible()  # what was done, by domain
    assert "secret.txt" not in desktop.inner_text("body")


# ── deep talk with a project ───────────────────────────────────────────────

def test_a_deep_talk_question_is_answered_in_the_conversation_and_leaves_the_panel(desktop, muninn):
    lid = muninn.h.engine.leases.attach(None, "garden")["lease_id"]  # the latest conversation: the stream goes on with it
    muninn.relay_asks(lid, 1, "Which hint first?", "Range", "Guesses left")
    go(desktop, muninn)
    t = talk(desktop)
    button(t.get_by_label("garden 問你"), "Range", exact=True).click()
    t.get_by_text("你選了:Range").wait_for()
    open_panel(desktop)
    expect()(waiting(desktop, "Which hint first?")).to_have_count(0)
    assert muninn.desk.result(f"{lid}:tu1") == {"v": 2, "decision": "allow", "answers": {"Which hint first?": "Range"}}
    muninn.relay_asks(lid, 2, "Keep a high score?", "Yes", "No")  # the next one arrives while it is open: shown live
    expect()(t.get_by_text("Keep a high score?")).to_have_count(1)


def test_a_sessions_steps_grow_as_a_small_line_and_fold_into_a_count_and_muninn_speaks_beside_it(desktop, muninn):
    lid = muninn.h.engine.leases.attach(None, "garden")["lease_id"]
    go(desktop, muninn)
    t = talk(desktop)
    expect()(desktop.get_by_text("跟 garden 深談")).to_have_count(1)
    expect()(button(desktop, "收回", exact=True)).to_have_count(0)  # it comes back by itself
    muninn.session_streams(lid, {"type": "tool_use", "kind": "read", "summary": "讀 README.md"})
    t.get_by_text("讀 README.md⋯").wait_for()
    muninn.session_streams(lid, {"type": "tool_use", "kind": "other", "summary": "問 Ada:要用哪種土?"})
    t.get_by_text("要用哪種土?⋯", exact=True).wait_for()  # a question the runner relays, without the owner's name it puts first
    muninn.session_streams(lid, {"type": "tool_use", "kind": "run_command", "summary": "跑 python try.py"},
                           {"type": "assistant", "text": "跑完了"}, {"type": "turn_end"})
    t.get_by_text("做了 3 步").wait_for()
    t.get_by_text("做了 3 步").click()
    t.get_by_text("跑 python try.py").wait_for()  # each step, once opened, as the runner said it
    muninn.session_streams(lid, {"type": "sidecar", "text": "好,明天提醒你打給水電行。"})
    t.get_by_text("好,明天提醒你打給水電行。").wait_for()
    muninn.session_streams(lid, {"type": "relay", "text": "排水管先挖 30 公分。"})
    t.locator(".receipt", has_text="告訴 garden:排水管先挖 30 公分。").wait_for()  # what the session heard of it
    muninn.session_streams(lid, {"type": "error", "error": "process_crashed"})
    expect()(button(desktop, "收回", exact=True)).to_have_count(1)  # stuck: now it can be taken back by hand


def test_a_photo_is_picked_goes_up_at_once_and_is_sent_with_what_is_said(desktop, muninn, tmp_path):
    import base64

    photo = tmp_path / "客廳.png"
    photo.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="))
    go(desktop, muninn)
    desktop.locator("#say input[type=file]").set_input_files(str(photo))
    chip = desktop.locator("#say-files .chip")
    expect()(chip).to_have_count(1)
    expect()(desktop.locator("#say-files .chip.going")).to_have_count(0)  # up before anything is sent
    with desktop.expect_request(lambda r: r.url.endswith("/api/command")) as sent:
        say(desktop, "這是客廳")
    [uid] = sent.value.post_data_json["attachments"]
    assert muninn.uploads.get(uid).name == "客廳.png"
    expect()(talk(desktop).locator(".said .sent.photo img")).to_have_count(1)
    expect()(chip).to_have_count(0)  # sent: the row under the input is empty again


def test_a_photo_sent_in_a_deep_talk_is_still_there_after_the_page_is_opened_again(desktop, muninn):
    import base64

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
    got = muninn.h.engine.leases.attach(None, "garden")
    meta = muninn.uploads.save("door.png", "image/png", png)
    muninn.h.engine.leases.send(got["thread"], "門板高 227 公分", None, [muninn.uploads.get(meta["id"])])
    go(desktop, muninn)
    photo = talk(desktop).locator(".said .sent.photo img")
    expect()(photo).to_have_count(1)
    desktop.wait_for_function("n => n.complete", arg=photo.element_handle())  # (drawn before it has come)
    assert photo.evaluate("n => n.naturalWidth") == 1  # the photo itself, read back from the server
    talk(desktop).get_by_text("門板高 227 公分").wait_for()


def test_a_sessions_markdown_reads_as_bold_and_code_and_its_html_stays_text(desktop, muninn):
    lid = muninn.h.engine.leases.attach(None, "garden")["lease_id"]
    muninn.h.engine.leases.on_event({"v": 1, "seq": 1, "lease_id": lid, "ts": "", "type": "assistant",
                                     "text": "看 **這裡** 跟 `code` 和 <b>raw</b>"})
    go(desktop, muninn)
    t = talk(desktop)
    t.get_by_text("這裡", exact=True).wait_for()
    assert t.get_by_text("這裡", exact=True).evaluate("n => getComputedStyle(n).fontWeight") in ("700", "bold", "600")
    t.get_by_text("<b>raw</b>").wait_for()  # HTML the session writes is shown as text, never as markup


def test_an_ideas_sandbox_starts_able_to_act_and_each_write_is_asked(desktop, muninn):
    muninn.runner.sandboxes = ["idea-4"]
    lid = muninn.h.engine.leases.attach(None, "idea-4")["lease_id"]
    go(desktop, muninn)
    t = talk(desktop)
    expect()(desktop.get_by_text("可動手(每次先問你)")).to_have_count(1)
    expect()(button(t, "允許動手", exact=True)).to_have_count(0)  # nothing to switch on first
    muninn.relay_wants(lid, 1, "idea-4", "Write", "write_file", "notes/plan.md")
    muninn.relay_wants(lid, 2, "idea-4", "Bash", "run_command", "python try.py")
    write = t.get_by_label("idea-4 想改檔案")
    run = t.get_by_label("idea-4 想跑指令")
    expect()(button(write, "這次深談都允許改這個資料夾", exact=True)).to_have_count(1)
    expect()(run.get_by_role("button")).to_have_count(2)  # a command is allowed once or refused, never for the whole talk
    button(run, "允許這一次", exact=True).click()
    t.get_by_text("允許跑:python try.py").wait_for()
    assert muninn.desk.result(f"{lid}:w2") == {"v": 2, "decision": "allow"}
    open_panel(desktop)
    expect()(waiting(desktop, "想改 notes/plan.md")).to_have_count(1)  # the one still open waits in the panel too


# ── a new user ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("screen", SCREENS)
def test_a_new_user_sees_every_place_and_where_to_start(request, newcomer, screen):
    pg = request.getfixturevalue(screen)
    for where, shown in (("", None), ("things", "今天"), ("today", "做了"), ("progress", "還沒有目標"), ("lists", "清單"), ("settings", "記一筆")):
        go(pg, newcomer, where, shown)
        no_stray_text(pg)
    go(pg, newcomer)
    start = talk(pg).get_by_role("region", name="從這裡開始")
    start.get_by_role("link", name="接上行事曆、信件、位置").click()  # where the sources are set up
    pg.get_by_text("記一筆").first.wait_for()
    go(pg, newcomer)
    talk(pg).get_by_role("region", name="從這裡開始").get_by_role("button", name="開始").click()  # telling Muninn what matters
    assert say_box(pg).input_value() == "我最近在意的是:"


def test_a_user_with_domains_gets_no_start_card(page, muninn):
    go(page, muninn)
    assert page.get_by_role("region", name="從這裡開始").count() == 0


def test_a_new_user_starts_by_saying_what_matters_and_gets_a_domain(desktop, newcomer):
    newcomer.replies({"actions": [{"tool": "propose_domain", "args": {"id": "job-search", "title": "換工作", "hours_per_week": 6,
                                                                       "deadlines": [], "why": "他說年底前想換工作"}}],
                      "reply": "幫你開了「換工作」,每一項都等你說要。"})
    go(desktop, newcomer)
    talk(desktop).get_by_role("region", name="從這裡開始").get_by_role("button", name="開始").click()
    box = say_box(desktop)
    with desktop.expect_request(lambda r: r.url.endswith("/api/command")) as sent:
        box.press("End")
        desktop.keyboard.type("年底前想換工作")
        box.press("Enter")
    assert sent.value.post_data_json["about"] == {"kind": "onboarding", "id": "first"}
    talk(desktop).get_by_text("幫你開了「換工作」").wait_for()
    assert [p.id for p in newcomer.h.engine.plans.load()] == ["job-search"]


# ── one stream, many topics ────────────────────────────────────────────────

def test_a_sentence_about_another_conversation_goes_there_with_a_divider(desktop, muninn):
    store = muninn.h.store
    store.save_thread("dentist", {"about": None, "ts": "2026-10-22T09:00:00+08:00", "turns": [
        {"role": "user", "text": "明天幾點看牙醫?", "ts": "x"}, {"role": "muninn", "text": "明天 10:30。", "ts": "x"}]})
    store.save_thread("room", {"about": None, "ts": "2026-10-22T09:30:00+08:00", "turns": [
        {"role": "user", "text": "先看房間重建準不準", "ts": "x"}, {"role": "muninn", "text": "照片傳上來就開始。", "ts": "x"}]})
    muninn.replies({"topic": "dentist"}, {"actions": [], "reply": "要帶健保卡。"})  # the local model judges, then answers
    go(desktop, muninn)
    say(desktop, "那牙醫要帶健保卡嗎?")
    t = talk(desktop)
    t.get_by_text("換到:明天幾點看牙醫?").wait_for()
    t.get_by_text("要帶健保卡。").wait_for()
    assert store.thread("dentist")["turns"][-2]["text"] == "那牙醫要帶健保卡嗎?"  # kept with the dentist conversation


@pytest.mark.parametrize("screen", SCREENS)
def test_a_topic_waiting_on_you_is_listed_and_closing_it_by_saying_so_moves_it_to_closed(request, muninn, screen):
    pg = request.getfixturevalue(screen)
    muninn.replies({"actions": [], "reply": "10:30,要提醒你嗎?", "topic": {"title": "牙醫", "state": "open", "now": "要不要提醒"}, "question_for_him": "要不要提醒"})
    go(pg, muninn)
    say(pg, "明天幾點看牙醫?")
    talk(pg).get_by_text("10:30,要提醒你嗎?").wait_for()
    open_panel(pg)
    expect()(waiting(pg, "牙醫")).to_have_count(1)
    pg.goto(f"{muninn.url}/#")  # back to the conversation
    muninn.replies({"actions": [], "reply": "好,10:00 提醒你。", "topic": {"title": "牙醫", "state": "closed", "now": "10:00 提醒"}})
    say(pg, "好")
    talk(pg).get_by_text("好,10:00 提醒你。").wait_for()
    open_panel(pg)
    expect()(waiting(pg, "牙醫")).to_have_count(0)
    place(pg, "清單")
    expect()(panel(pg).get_by_role("region", name="今天收了的話題").get_by_role("button", name="牙醫")).to_have_count(1)


def test_when_the_user_pauses_muninn_asks_one_thing_in_one_sentence_and_it_is_answered_there(desktop, muninn):
    from datetime import timedelta

    seed(muninn)
    muninn.replies({"actions": [], "reply": "好"})
    go(desktop, muninn)
    say(desktop, "今天先做投遞")
    talk(desktop).get_by_text("好", exact=True).wait_for()
    muninn.h.clock.t += timedelta(minutes=25)
    said = muninn.h.engine.speak_up().run()
    assert said
    desktop.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")  # the app comes back to the front
    t = talk(desktop)
    t.get_by_text(said).wait_for()
    expect()(t.get_by_text("件等你")).to_have_count(0)  # one thing, said; not a list
    button(t, "要", exact=True).click()  # the buttons that go with the sentence
    eventually(lambda: not [c for c in muninn.h.engine.pending_confirmations() if c.kind == "action"])



def test_two_things_muninn_brings_up_each_keep_their_own_button_under_them(desktop, muninn):
    from datetime import timedelta

    conv = muninn.h.engine.conversation()
    tid = conv.speak(None, "上線了:「澆水摘要」", [{"label": "還沒好", "tool": "capture_idea", "args": {"idea": "a"}}])
    muninn.h.clock.t += timedelta(seconds=1)
    conv.speak(None, "上線了:「草圖直接看」", [{"label": "還沒好", "tool": "capture_idea", "args": {"idea": "b"}}], thread=tid)
    go(desktop, muninn)
    t = talk(desktop)
    for line in ("澆水摘要", "草圖直接看"):  # the button right after its own line
        t.get_by_text(f"上線了:「{line}」").wait_for()
        mine = t.locator(f"xpath=//*[contains(@class,'reply') and contains(., '{line}')]/following-sibling::*[1]")
        expect()(mine.get_by_role("button", name="還沒好")).to_have_count(1)
    button(t, "還沒好").first.click()
    eventually(lambda: [x["args"]["idea"] for x in conv.thread(tid)["suggest"]] == ["b"])


def test_a_question_said_again_has_one_row_of_buttons_and_a_press_leaves_none(desktop, muninn):
    from muninn.core.actions import DecidedBy, TrackerStatus

    h = muninn.h
    h.engine.gate.run(TrackerStatus("jobs", "acme", "interview", "Acme 寄來面試邀請"), DecidedBy.INFERRED_UNSURE)
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    buttons = [{"label": o, "tool": "answer_question", "args": {"answer": o}} for o in c.options]
    conv = h.engine.conversation()
    conv.speak({"kind": "question", "id": c.id}, c.question, buttons)
    go(desktop, muninn)
    t = talk(desktop)
    expect()(button(t, c.options[1], exact=True)).to_have_count(1)
    h.clock.t += timedelta(hours=11)  # still open the next day: said again, to the open screen
    conv.speak({"kind": "question", "id": c.id}, c.question, buttons)
    expect()(t.get_by_text(c.question)).to_have_count(2)
    expect()(button(t, c.options[1], exact=True)).to_have_count(1)  # under the latest saying only
    button(t, c.options[1], exact=True).click()
    t.get_by_text(f"按了「{c.options[1]}」").wait_for()
    expect()(t.locator(".suggest")).to_have_count(0)  # answered: nothing left to press, nothing drawn again under it
    go(desktop, muninn)  # and so after a reload
    t.get_by_text(f"按了「{c.options[1]}」").wait_for()
    expect()(talk(desktop).locator(".suggest")).to_have_count(0)


# ── two devices, one conversation ──────────────────────────────────────────

def test_what_was_said_on_one_device_is_in_the_others_conversation_when_it_comes_back(request, muninn):
    phone, desk = request.getfixturevalue("page"), request.getfixturevalue("desktop")
    go(phone, muninn)
    go(desk, muninn)
    seen = "Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => '%s'});" \
           "document.dispatchEvent(new Event('visibilitychange'))"
    phone.evaluate(seen % "hidden")  # the phone put away: its stream of changes closes
    muninn.replies({"actions": [], "reply": "明天 10:30,在牙醫診所。"})
    say(desk, "明天幾點看牙醫?")
    talk(desk).get_by_text("明天 10:30,在牙醫診所。").wait_for()
    phone.wait_for_timeout(1500)  # longer than the server takes to tell open screens
    expect()(talk(phone).get_by_text("明天幾點看牙醫?")).to_have_count(0)  # the phone was away meanwhile
    phone.evaluate(seen % "visible")  # back to it
    expect()(talk(phone).get_by_text("明天幾點看牙醫?")).to_have_count(1)
    expect()(talk(phone).get_by_text("明天 10:30,在牙醫診所。")).to_have_count(1)
    phone.evaluate("window.dispatchEvent(new Event('focus'))")  # nothing new: drawn once, not twice
    expect()(talk(phone).get_by_text("明天 10:30,在牙醫診所。")).to_have_count(1)


def test_an_open_screen_shows_what_was_said_on_another_device_by_itself(request, muninn):
    phone, desk = request.getfixturevalue("page"), request.getfixturevalue("desktop")
    go(phone, muninn)
    go(desk, muninn)
    muninn.replies({"actions": [], "reply": "後天下午兩點,在牙醫診所。"})
    say(desk, "後天幾點看牙醫?")
    talk(desk).get_by_text("後天下午兩點,在牙醫診所。").wait_for()
    expect()(talk(phone).get_by_text("後天下午兩點,在牙醫診所。")).to_have_count(1)  # nobody touched the phone
    expect()(talk(desk).get_by_text("後天下午兩點,在牙醫診所。")).to_have_count(1)  # the desk's own change: not drawn twice


def test_nothing_is_redrawn_under_a_button_pressed_and_what_changed_meanwhile_still_shows(request, muninn):
    phone, desk = request.getfixturevalue("page"), request.getfixturevalue("desktop")
    go(phone, muninn)
    go(desk, muninn)
    muninn.replies({"actions": [], "reply": "要我提醒你嗎?", "suggest": [{"label": "好,提醒我", "tool": "capture_idea", "args": {"idea": "週五繳電費"}}]})
    say(phone, "週五要繳電費")
    held = []
    phone.route("**/api/command/act", lambda r: held.append(r))
    button(talk(phone), "好,提醒我").click()
    eventually(lambda: held)  # the press is on its way
    # what changes meanwhile elsewhere: Muninn saying something in a conversation of its own (said on the desk, the
    # sentence would go on whichever conversation the desk last drew, which depends on timing)
    muninn.h.engine.conversation().speak(None, "明晚七點,在河濱公園。", [])
    talk(desk).get_by_text("明晚七點,在河濱公園。").wait_for()
    phone.wait_for_timeout(2500)  # the change has come by now
    expect()(talk(phone).get_by_text("按了「好,提醒我」")).to_have_count(0)  # not redrawn under the press
    pressed = []
    phone.on("requestfinished", lambda r: pressed.append(r) if "/api/command/act" in r.url else None)
    held[0].continue_()
    talk(phone).get_by_text("按了「好,提醒我」").wait_for()
    eventually(lambda: pressed)  # the press done with: a redraw asked while it is still going is put off
    for _ in range(3):  # coming back to the app: what changed meanwhile, drawn after
        phone.evaluate("window.dispatchEvent(new Event('focus'))")
        if talk(phone).get_by_text("明晚七點,在河濱公園。").count():
            break
        phone.wait_for_timeout(1000)
    expect()(talk(phone).get_by_text("明晚七點,在河濱公園。")).to_have_count(1)


def test_a_read_already_on_its_way_when_a_button_is_pressed_does_not_redraw_under_it(page, muninn):
    go(page, muninn)
    muninn.replies({"actions": [], "reply": "要我提醒你嗎?", "suggest": [{"label": "好,提醒我", "tool": "capture_idea", "args": {"idea": "週五繳電費"}}]})
    say(page, "週五要繳電費")
    button(talk(page), "好,提醒我").wait_for()
    held = []
    page.route("**/api/conversation", lambda r: r.continue_() if held else held.append(r))
    page.evaluate("window.dispatchEvent(new Event('focus'))")  # back to the app: the conversation is read again
    eventually_on(page, lambda: held)
    muninn.h.engine.conversation().speak(None, "明晚七點,在河濱公園。", [])  # so what comes back differs from what is shown
    page.route("**/api/command/act", lambda r: (held.append(r), None))
    button(talk(page), "好,提醒我").click()
    eventually_on(page, lambda: len(held) == 2)  # the press is on its way
    held[0].continue_()  # and the read comes back meanwhile
    page.wait_for_timeout(500)
    expect()(talk(page).get_by_text("明晚七點,在河濱公園。")).to_have_count(0)  # not redrawn under the press
    held[1].continue_()
    talk(page).get_by_text("按了「好,提醒我」").wait_for(timeout=15_000)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect()(talk(page).get_by_text("明晚七點,在河濱公園。")).to_have_count(1)  # drawn when it can be


def test_a_talk_that_comes_back_shows_what_is_yours_first_and_reads_the_same_after_a_reload(request, muninn):
    pg = request.getfixturevalue("desktop")
    go(pg, muninn)
    got = muninn.h.engine.leases.attach(None, "garden")
    lid = got["lease_id"]
    handover = {"summary": "排水談到坡度,還差後院的長度。", "decisions": [{"what": "坡度 2%", "why": "雨水才排得掉"}],
                "open_questions": ["要不要加集水井?"], "tasks_for_owner": [{"text": "量後院長度"}]}
    muninn.h.engine.leases.on_event({"v": 1, "seq": 1, "lease_id": lid, "type": "handover", "handover": handover})
    muninn.h.engine.leases.on_event({"v": 1, "seq": 2, "lease_id": lid, "type": "state", "state": "detached"})
    for _ in range(2):  # drawn from the conversation, then again after a reload
        go(pg, muninn)
        card = pg.get_by_role("region", name="跟 garden 談完了")
        expect()(card.get_by_text("要你做的 · 1")).to_have_count(1)
        expect()(card.get_by_text("量後院長度")).to_have_count(1)
        expect()(card.get_by_text("要不要加集水井?")).to_have_count(1)
        expect()(card.get_by_text("坡度 2%")).not_to_be_visible()  # what was decided, folded away
        card.get_by_text("決定了 1 件").click()
        expect()(card.get_by_text("坡度 2%")).to_be_visible()



# ── the panel opens at once ─────────────────────────────────────────────────

def test_the_panel_opens_at_once_from_what_is_held_and_a_view_with_its_own_data_fills_in(request, muninn):
    pg = request.getfixturevalue("page")
    seed(muninn)
    flying, live = set(), []
    pg.on("request", lambda r: flying.add(r) if "/api/" in r.url and "/events" not in r.url else None)
    pg.on("requestfinished", lambda r: flying.discard(r))
    pg.on("requestfailed", lambda r: flying.discard(r))
    pg.on("response", lambda r: live.append(r) if "/api/changes/events" in r.url else None)
    go(pg, muninn)
    expect()(pill(pg)).to_have_accessible_name(pytest.importorskip("re").compile(r"件"))  # what waits, read once
    quiet = 0
    for _ in range(100):  # the page settled: its stream of changes open (which reads once more), nothing on its way for
        pg.wait_for_timeout(100)  # longer than the server takes to notice what the test wrote and say so (screen_watch.LOOK_S)
        quiet = quiet + 1 if live and not flying else 0
        if quiet >= 15:
            break
    held = []
    pg.route("**/api/**", lambda route: held.append(route))  # from now on no read is answered until let go
    pill(pg).click()
    panel(pg).get_by_text("今天", exact=False).first.wait_for(timeout=1000)  # opened though nothing is answered
    expect()(panel(pg).get_by_text("等你")).to_have_count(1)  # today's things: drawn from what is held
    pg.wait_for_timeout(150)
    assert [r.request.url for r in held if "/api/changes" not in r.request.url] == []  # nothing read again to open it
    panel(pg).get_by_role("link", name=pytest.importorskip("re").compile("^今天")).first.click()
    expect()(panel(pg).get_by_label("讀取中")).to_have_count(1)  # its own data on the way: a skeleton, not a wait
    pg.unroute("**/api/**")
    for r in held:
        r.continue_()
    expect()(panel(pg).get_by_label("讀取中")).to_have_count(0)  # filled in once it came


def test_a_question_that_takes_several_answers_is_answered_with_all_of_them(desktop, muninn):
    lid = muninn.h.engine.leases.attach(None, "garden")["lease_id"]
    muninn.relay_asks_v2(lid, 1, "Which rooms to measure?", "Bedroom", "Kitchen", "Hall", multi=True)
    go(desktop, muninn)
    card = talk(desktop).get_by_label("garden 問你")
    expect()(button(card, "送出", exact=True)).to_have_count(0)  # nothing picked yet
    button(card, "Bedroom", exact=True).click()
    button(card, "Hall", exact=True).click()
    button(card, "送出", exact=True).click()
    talk(desktop).get_by_text("你選了:Bedroom、Hall").wait_for()
    assert muninn.desk.result(f"{lid}:q1") == {"v": 2, "decision": "allow", "answers": {"Which rooms to measure?": ["Bedroom", "Hall"]}}


def test_a_talk_whose_project_is_renamed_shows_the_new_name_without_a_reload(desktop, muninn):
    ideas = muninn.h.engine.ideas
    ideas._sandbox = lambda name, purpose: name
    ideas._upgrade = lambda name, rename: rename or name
    n = ideas.capture("用照片重建房間")[1]
    muninn.runner.sandboxes = [f"idea-{n}"]
    ideas.explore(n)
    muninn.h.engine.leases.attach(None, f"idea-{n}")  # the latest conversation: the stream goes on with it
    go(desktop, muninn)
    expect()(desktop.get_by_text(f"跟 idea-{n} 深談", exact=False)).to_have_count(1)
    ideas.promote(n, "shelf-planner")  # said beside the talk, say: the sandbox is a project now, by a new name
    expect()(desktop.get_by_text("跟 shelf-planner 深談", exact=False)).to_have_count(1)
    expect()(say_box(desktop)).to_have_attribute("placeholder", "跟 shelf-planner 說⋯")


def test_a_rename_made_before_the_talks_stream_opens_is_not_missed(desktop, muninn):
    ideas = muninn.h.engine.ideas
    ideas._sandbox = lambda name, purpose: name
    ideas._upgrade = lambda name, rename: rename or name
    n = ideas.capture("用照片重建房間")[1]
    muninn.runner.sandboxes = [f"idea-{n}"]
    ideas.explore(n)
    muninn.h.engine.leases.attach(None, f"idea-{n}")
    held = []
    desktop.route("**/api/leases/*/events", lambda route: held.append(route))  # the talk's stream waits until the rename is made
    go(desktop, muninn)
    expect()(desktop.get_by_text(f"跟 idea-{n} 深談", exact=False)).to_have_count(1)
    while not held:
        desktop.wait_for_timeout(20)
    ideas.promote(n, "shelf-planner")
    held[0].continue_()
    expect()(desktop.get_by_text("跟 shelf-planner 深談", exact=False)).to_have_count(1)


def test_a_file_the_session_shows_is_seen_in_the_talk_and_stays_after_a_reload(desktop, muninn):
    import base64

    from e2e_support import expect_console

    expect_console(desktop, "status of 410")  # the expired one, on purpose
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    lid = muninn.h.engine.leases.attach(None, "garden")["lease_id"]
    muninn.runner.files = {(lid, "0123456789abcdef"): (png, "image/png"), (lid, "00000000000000aa"): (png, "image/png")}

    def shown(seq, fid, name, note):
        return {"v": 2, "seq": seq, "lease_id": lid, "ts": "", "type": "file", "id": fid, "path": f"results/{name}",
                "name": name, "mime": "image/png", "size": 10, "summary": note}

    muninn.h.engine.leases.on_event(shown(1, "0123456789abcdef", "plan.png", "排水平面圖"))  # shown while this screen was away
    muninn.h.engine.leases.on_event(shown(2, "fedcba9876543210", "old.png", "上週的圖"))
    now = muninn.h.clock.t
    muninn.h.clock.t = now + timedelta(days=31)  # read a month on: old.png is past the days it is kept
    muninn.h.engine.store.set_state("talk:last_seen", muninn.h.clock.now().isoformat())  # and here all along: no opening folds the talk away
    t = talk(desktop)
    for _ in range(2):  # drawn from the talk as kept, and again after a reload
        go(desktop, muninn)
        expect()(t.get_by_role("img", name="plan.png")).to_be_visible()
        expect()(t.get_by_text("排水平面圖")).to_have_count(1)
        expect()(t.get_by_text("old.png:檔案已經過期")).to_have_count(1)  # gone (kept 30 days): said so
    expect()(desktop.get_by_text("跟 garden 深談", exact=False)).to_have_count(1)  # its stream open
    live = shown(3, "00000000000000aa", "live.png", "剛畫好的")
    muninn.h.engine.leases.on_event(live)
    for _ in range(5):  # as it comes (sent again if this screen's stream was still connecting)
        muninn.session_streams(lid, live)
        try:
            expect()(t.get_by_role("img", name="live.png").first).to_be_visible(timeout=1000)
            break
        except AssertionError:
            continue
    expect()(t.get_by_role("img", name="live.png").first).to_be_visible()


def test_a_phone_opens_on_the_conversation_whatever_address_the_app_or_a_notification_brings(page, muninn):
    seed(muninn)
    for where in ("today", "things"):
        page.goto("about:blank")  # each a launch of its own
        go(page, muninn, where)
        assert panel(page).is_hidden() and say_box(page).is_visible()
    go(page, muninn, "settings", "設定")  # a place asked for on purpose (after pairing) still opens
    assert panel(page).is_visible()


def test_a_photo_tapped_on_a_phone_opens_over_the_conversation_and_closes(page, muninn):
    import base64

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
    got = muninn.h.engine.leases.attach(None, "garden")
    meta = muninn.uploads.save("door.png", "image/png", png)
    muninn.h.engine.leases.send(got["thread"], "門板高 227 公分", None, [muninn.uploads.get(meta["id"])])
    go(page, muninn)
    start = page.url
    photo = talk(page).locator(".said .sent.photo")
    photo.click()
    viewer = page.get_by_role("dialog", name="door.png")
    expect()(viewer).to_be_visible()
    assert page.url == start and len(page.context.pages) == 1  # the app stays where it was: no tab it cannot leave
    viewer.get_by_role("button", name="關閉").click()
    expect()(viewer).to_have_count(0)
    photo.click()
    page.locator("#viewer img").click()  # a tap on the picture closes it too
    expect()(page.locator("#viewer")).to_have_count(0)
    photo.click()
    page.keyboard.press("Escape")
    expect()(page.locator("#viewer")).to_have_count(0)
    assert say_box(page).is_visible()


def test_a_picture_not_there_yet_is_asked_for_again_and_never_called_expired(desktop, muninn):
    import base64
    import threading

    from e2e_support import expect_console

    expect_console(desktop, "status of 503")  # not there for the first asks, on purpose
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    lid = muninn.h.engine.leases.attach(None, "garden")["lease_id"]
    muninn.runner.files = {}
    muninn.h.engine.leases.on_event({"v": 2, "seq": 1, "lease_id": lid, "ts": "", "type": "file", "id": "00000000000000bb",
                                     "path": "results/sofa.png", "name": "sofa.png", "mime": "image/png", "size": 10})
    threading.Timer(1.5, lambda: muninn.runner.files.update({(lid, "00000000000000bb"): (png, "image/png")})).start()
    go(desktop, muninn)
    t = talk(desktop)
    expect()(t.get_by_role("img", name="sofa.png")).to_be_visible(timeout=15000)
    assert t.get_by_text("過期").count() == 0


def test_an_answer_picked_leaves_what_waits_at_once_and_its_page_goes_back(desktop, muninn):
    seed(muninn)
    [c] = [c for c in muninn.h.engine.pending_confirmations() if c.kind == "action"]
    go(desktop, muninn)
    open_panel(desktop)
    waiting(desktop, c.question).first.click()
    with desktop.expect_response(lambda r: f"/api/confirmations/{c.id}" in r.url):
        panel(desktop).locator(".box.item .options button").first.click()
        expect()(waiting(desktop, c.question)).to_have_count(0)  # gone before the server has answered
    assert desktop.url.endswith("#things")
    expect()(desktop.get_by_text("已記下")).to_be_visible()
    assert c.id not in [x.id for x in muninn.h.engine.pending_confirmations()]


def test_a_question_answered_in_the_panel_leaves_no_buttons_in_the_conversation(desktop, muninn):
    seed(muninn)
    [c] = [c for c in muninn.h.engine.pending_confirmations() if c.kind == "action"]
    muninn.h.engine.conversation().speak({"kind": "question", "id": c.id}, c.question,
                                         [{"label": o, "tool": "answer_question", "args": {"answer": o}} for o in c.options])
    go(desktop, muninn)
    t = talk(desktop)
    expect()(t.locator(".suggest")).to_have_count(1)
    open_panel(desktop)
    waiting(desktop, c.question).first.click()
    panel(desktop).locator(".box.item .options button").first.click()
    eventually(lambda: c.id not in [x.id for x in muninn.h.engine.pending_confirmations()])
    expect()(t.locator(".suggest")).to_have_count(0)  # what they answered was answered in the panel: nothing left to press


def test_what_is_typed_into_a_card_outlives_a_change_drawn_while_it_is_not_focused(desktop, muninn):
    seed(muninn)
    go(desktop, muninn)
    open_panel(desktop)
    waiting(desktop, "Red or white roses?").first.click()
    box = panel(desktop).locator(".box.item textarea")
    box.fill("白的,種在東邊那排")
    panel(desktop).locator(".panel-head").click()  # elsewhere: the box is no longer focused
    box.evaluate("b => { b.dataset.before = '1' }")
    muninn.h.engine.messages.receive([{"id": "m9", "project": "garden", "type": "question", "text": "Which hose length?"}])
    desktop.evaluate("window.dispatchEvent(new Event('focus'))")  # and the app comes back into view
    drawn = panel(desktop).locator(".box.item textarea:not([data-before])")
    drawn.wait_for()  # the change has come and the card is drawn again
    expect()(drawn).to_have_value("白的,種在東邊那排")


def test_a_sentence_that_could_not_be_sent_is_back_in_the_box(desktop, muninn):
    go(desktop, muninn)
    desktop.route("**/api/command", lambda r: r.abort())
    say(desktop, "明天提醒我繳電費")
    talk(desktop).get_by_text("沒有送出去", exact=False).wait_for()
    expect()(say_box(desktop)).to_have_value("明天提醒我繳電費")


def test_every_line_says_when_and_the_day_is_named_where_it_changes(page, muninn):
    import re

    h = muninn.h
    h.store.save_thread("t-old", {"about": None, "ts": "2026-10-20T21:05:00+08:00", "turns": [
        {"role": "user", "text": "沙發量好了嗎?", "ts": "2026-10-20T21:05:00+08:00"},
        {"role": "muninn", "text": "還沒,明天量。", "ts": "2026-10-20T21:06:00+08:00"}]})
    h.store.save_thread("t-new", {"about": None, "ts": "2026-10-21T09:15:00+08:00", "turns": [
        {"role": "user", "text": "量好了 210 公分", "ts": "2026-10-21T09:15:00+08:00"},
        {"role": "muninn", "text": "記下了。", "ts": "2026-10-21T09:15:30+08:00"}]})
    go(page, muninn)
    t = talk(page)
    t.locator("details.earlier > summary").click()  # both days are before today: folded at the top
    said = t.locator(".said", has_text="量好了 210 公分")
    expect()(said.locator("time.stamp")).to_have_text(re.compile(r"^(上午|下午)\s?9:15$"))
    expect()(t.locator(".reply", has_text="記下了。").locator("time.stamp")).to_have_text(re.compile(r"^上午\s?9:15$"))
    days = t.locator(".day")
    expect()(days).to_have_count(2)  # one where each day starts, never beside the times
    assert days.nth(0).inner_text().startswith("10/20") and days.nth(1).inner_text() == "昨天"  # the app's today is 10/22
    expect()(t.get_by_text("量好了 210 公分", exact=True)).to_be_visible()  # the words themselves stay as said


def test_what_was_said_in_a_deep_talk_stays_in_the_conversation_after_it_ends(page, muninn):
    from datetime import timedelta

    leases, clock, store = muninn.h.engine.leases, muninn.h.clock, muninn.h.store
    event = lambda lid, n, kind, **d: leases.on_event({"v": 2, "seq": n, "lease_id": lid, "ts": "", "type": kind, **d})  # noqa: E731

    def said(tid, text):  # what he says to Muninn: kept in the conversation, then Muninn goes into the talk
        clock.t += timedelta(minutes=1)
        t = store.thread(tid) or {"about": None, "turns": []}
        store.save_thread(tid, {**t, "ts": clock.now().isoformat(),
                                "turns": [*t["turns"], {"role": "user", "text": text, "ts": clock.now().isoformat(), "cloud_ok": True}]})

    said("t1", "第一輪:玫瑰要種哪裡?")
    lid = leases.lend("t1", "garden")["lease_id"]
    clock.t += timedelta(minutes=1)
    event(lid, 1, "assistant", text="東邊的花台日照最好。")
    clock.t += timedelta(minutes=1)
    event(lid, 2, "handover", handover={"v": 2, "summary": "第一輪談好了", "decisions": [], "open_questions": [], "tasks_for_owner": []})
    event(lid, 3, "state", state="detached")
    clock.t += timedelta(minutes=5)
    said("t1", "第二輪:要不要加滴灌?")
    assert leases.lend("t1", "garden")["lease_id"] == lid  # gone into again: the same talk goes on
    clock.t += timedelta(minutes=1)
    event(lid, 4, "assistant", text="加,一天兩次。")
    clock.t += timedelta(minutes=1)
    event(lid, 5, "handover", handover={"v": 2, "summary": "第二輪談好了", "decisions": [], "open_questions": [], "tasks_for_owner": []})
    event(lid, 6, "state", state="detached")
    go(page, muninn)
    t = talk(page)
    folds = t.locator("details.talk-fold")
    expect()(folds).to_have_count(2)  # each stretch, folded
    for seen in ("第一輪:玫瑰要種哪裡?", "第二輪:要不要加滴灌?", "第一輪談好了", "第二輪談好了"):
        expect()(t.get_by_text(seen, exact=True)).to_be_visible()  # what went into it and how it ended, however often
    expect()(t.locator("#log > .receipt", has_text="garden")).to_have_count(2)  # where each began, kept in the conversation
    expect()(t.get_by_text("東邊的花台日照最好。", exact=True)).to_be_hidden()  # what was said in it, folded
    for fold in (folds.nth(0), folds.nth(1)):
        fold.locator("summary").click()
    order = ["第一輪:玫瑰要種哪裡?", "東邊的花台日照最好。", "第一輪談好了", "第二輪:要不要加滴灌?", "加,一天兩次。", "第二輪談好了"]
    for words in order:
        expect()(t.get_by_text(words, exact=True)).to_have_count(1)  # each once
    ys = [t.get_by_text(words, exact=True).bounding_box()["y"] for words in order]
    assert ys == sorted(ys)  # as it happened


def test_shift_enter_starts_a_new_line_and_enter_says_it(desktop, muninn):
    go(desktop, muninn)
    box = say_box(desktop)
    box.click()
    box.type("第一行")
    with desktop.expect_request(lambda r: r.url.endswith("/api/command")) as sent:
        box.press("Shift+Enter")
        box.type("第二行")
        height = box.bounding_box()["height"]
        box.evaluate("b => b.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true}))")
        assert box.input_value() == "第一行\n第二行"  # picking a word in an input method sends nothing
        box.press("Enter")
    assert sent.value.post_data_json["text"] == "第一行\n第二行"
    assert box.input_value() == "" and box.bounding_box()["height"] < height  # back to one line
    expect()(talk(desktop).locator(".said", has_text="第一行")).to_contain_text("第二行")


def test_the_conversation_opens_on_today_with_what_came_before_folded_above(page, muninn):
    from datetime import timedelta

    now = muninn.h.clock.now()
    then = (now - timedelta(days=1)).replace(hour=21, minute=5)
    stamp = lambda d: d.isoformat()  # noqa: E731
    muninn.h.store.save_thread("t-old", {"about": None, "ts": stamp(then), "turns": [
        {"role": "user", "text": "沙發量好了嗎?", "ts": stamp(then)},
        {"role": "muninn", "text": "還沒,明天量。", "ts": stamp(then + timedelta(minutes=1))}],
        "suggest": [{"label": "先擱著沙發", "tool": "settle_topic", "args": {"topic": "沙發", "decision": "shelve"}}]})
    muninn.h.store.save_thread("t-new", {"about": None, "ts": stamp(now - timedelta(minutes=1)), "turns": [
        {"role": "user", "text": "量好了 210 公分", "ts": stamp(now - timedelta(minutes=2))},
        {"role": "muninn", "text": "記下了。", "ts": stamp(now - timedelta(minutes=1))}]})
    muninn.h.store.save_thread("t-back", {"about": None, "ts": stamp(now), "turns": [  # yesterday's, said in again today
        {"role": "user", "text": "窗簾要什麼顏色?", "ts": stamp(then + timedelta(minutes=5))},
        {"role": "muninn", "text": "窗簾的事還在等你。", "ts": stamp(now)}]})
    go(page, muninn)
    t = talk(page)
    expect()(t.get_by_text("量好了 210 公分", exact=True)).to_be_visible()  # today, as it is
    expect()(t.get_by_text("沙發量好了嗎?", exact=True)).to_be_hidden()  # before today, folded
    expect()(t.get_by_text("窗簾的事還在等你。", exact=True)).to_be_visible()
    expect()(t.get_by_text("窗簾要什麼顏色?", exact=True)).to_be_hidden()  # folded too, though below a line from today
    expect()(t.get_by_role("button", name="先擱著沙發")).to_be_hidden()  # the buttons yesterday's reply offered go with it
    expect()(t.locator(".day:visible")).to_have_text(["今天"])  # only today is named below the fold
    earlier = t.locator("details.earlier")
    expect()(earlier.locator("summary")).to_have_text("昨天以前的對話 · 3 則")
    earlier.locator("summary").click()
    expect()(t.get_by_text("沙發量好了嗎?", exact=True)).to_be_visible()
    expect()(earlier.locator(".day")).to_have_text("昨天")  # its day named inside it


def test_the_panel_lists_exactly_what_the_server_says_waits(desktop, muninn):
    seed(muninn)
    go(desktop, muninn)
    open_panel(desktop)
    listed = muninn.h.engine.waiting()
    assert listed  # the seeded world has things waiting
    expect()(panel(desktop).locator("a.row.two")).to_have_count(len(listed))


# ── first run ──────────────────────────────────────────────────────────────

def test_a_fresh_install_without_https_is_used_in_this_computers_browser_and_stays_paired(browser, tmp_path):
    from e2e_support import SETUP_TOKEN, Muninn

    m = Muninn(tmp_path, https=False)  # as installed: http on this computer, no HTTPS address yet
    _, b = browser
    ctx = b.new_context(service_workers="block")
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{m.url}/#setup={SETUP_TOKEN}")
    pg.get_by_role("button", name="先跳過,去配對手機").click()
    pg.get_by_text("先在這台電腦的瀏覽器用 Muninn").click()
    pg.get_by_role("textbox", name="跟 Muninn 說").wait_for()  # the app, in this browser
    cookie = next(c for c in ctx.cookies() if c["name"] == "muninn_device")
    assert cookie["secure"] is False  # Safari keeps no Secure cookie over plain http, even on this computer
    pg.reload()
    pg.get_by_role("textbox", name="跟 Muninn 說").wait_for()  # still paired after a reload
    pg.goto(f"{m.url}/#settings")  # the phone, later: its pairing needs an HTTPS address, which Tailscale gives
    pg.get_by_role("button", name="顯示配對碼").click()
    pg.get_by_role("button", name="幫我設定").click()
    pg.get_by_text("位址設好了").wait_for()  # not under a service manager here: the user restarts it
    assert "public_url: https://muninn.example.ts.net:8444" in (tmp_path / "config.yaml").read_text()
    assert errors == []
    m.server.should_exit = True
    ctx.close()


def test_a_first_run_sets_up_a_model_from_the_printed_link_and_pairs_a_phone_by_its_qr_code(browser, muninn):
    from e2e_support import SETUP_TOKEN

    _, b = browser
    ctx = b.new_context(ignore_https_errors=True, service_workers="block")
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{muninn.url}/#setup={SETUP_TOKEN}")
    pg.get_by_text("設定 Muninn").wait_for()
    pg.get_by_label("Anthropic API").check()
    pg.get_by_label("API key").fill("sk-ant-invented-e2e-key")
    pg.get_by_role("button", name="檢查 key,列出模型").click()
    pg.get_by_label("快的模型").select_option("model-small")
    pg.get_by_label("好的模型").select_option("model-large")
    pg.get_by_role("button", name="存檔").click()
    pg.get_by_text("重新啟動 muninn serve").wait_for()  # not under a service manager here: the user restarts it
    assert muninn.setup._cfg.data_dir.joinpath("config.yaml").read_text().count("model-large") == 1
    pg.goto(f"{muninn.url}/#setup={SETUP_TOKEN}")
    pg.get_by_role("button", name="先跳過,去配對手機").click()
    qr = pg.locator("img.pair-qr")
    qr.wait_for()
    url = qr.get_attribute("alt").removeprefix("配對網址 ")
    assert "?pair=" in url
    phone = ctx.new_page()
    phone.goto(url)  # what the phone's camera opens
    assert phone.get_by_label("配對碼").input_value() == url.split("pair=")[1]
    phone.get_by_role("button", name="配對").click()
    phone.get_by_role("textbox", name="跟 Muninn 說").wait_for()  # paired: the app
    assert errors == []
    ctx.close()


def test_coming_back_after_hours_away_opens_with_what_came_meanwhile(desktop, muninn):
    from muninn.core.models import InboxItem

    muninn.h.clock.t += timedelta(hours=4)
    muninn.h.engine.store.add_inbox(InboxItem(None, muninn.h.clock.now(), "health", "alert", "23:30 之後還在工作", "", {}, "anomaly:late-night"))
    go(desktop, muninn)
    talk(desktop).get_by_text("你不在的時候:23:30 之後還在工作", exact=False).wait_for()
    go(desktop, muninn)  # opened again a moment later: said once
    expect()(talk(desktop).get_by_text("你不在的時候", exact=False)).to_have_count(1)
