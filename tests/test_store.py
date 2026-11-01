

def test_turns_a_finished_talk_added_before_cards_get_its_card_once(tmp_path):
    import json as _json

    from muninn.adapters.store_sqlite import SqliteStore

    path = tmp_path / "m.db"
    s = SqliteStore(path)
    s.add_lease({"lease_id": "L1", "thread": "t1", "project": "garden", "mode": "discuss", "state": "detached",
                 "started": "2026-10-22T09:00", "ended": "2026-10-22T10:00",
                 "handover": {"summary": "排水談到坡度", "decisions": [{"what": "坡度 2%"}], "open_questions": [], "tasks_for_owner": []}})
    s.save_thread("t1", {"about": None, "ts": "2026-10-22T10:00", "turns": [
        {"role": "user", "text": "排水怎麼做", "ts": "x"},
        {"role": "muninn", "text": "跟 garden 的深談結束了。\n排水談到坡度", "ts": "y", "via": "lease:L1", "local_only": True}]})
    s2 = SqliteStore(path)  # started again: the old turn is given its card
    card = s2.thread("t1")["turns"][1]["handover"]
    assert card["project"] == "garden" and card["summary"] == "排水談到坡度" and not card["fallback"]
    assert "handover" not in s2.thread("t1")["turns"][0]


def test_a_talk_resumed_and_given_back_twice_shows_each_handover_as_it_was_said(tmp_path):
    from muninn.adapters.store_sqlite import SqliteStore
    from muninn.core.lease import handover_text
    from conftest import ZH

    path = tmp_path / "m.db"
    s = SqliteStore(path)
    first = {"summary": "選了臥室", "decisions": [{"what": "先測臥室"}], "open_questions": ["哪天拍?"], "tasks_for_owner": [{"text": "量臥室"}]}
    s.add_lease({"lease_id": "L1", "thread": "t1", "project": "idea-7", "mode": "work", "state": "detached",
                 "started": "2026-10-22T09:00", "ended": "2026-10-22T12:00", "handover": {"fallback": True, "summary": "x"}})
    s.save_thread("t1", {"about": None, "ts": "2026-10-22T12:00", "turns": [
        {"role": "muninn", "text": handover_text("idea-7", first, words=ZH), "ts": "a", "via": "lease:L1", "local_only": True},
        {"role": "muninn", "text": handover_text("idea-7", {"fallback": True}, words=ZH), "ts": "b", "via": "lease:L1", "local_only": True}]})
    turns = SqliteStore(path).thread("t1")["turns"]
    assert turns[0]["handover"]["decisions"] == [{"what": "先測臥室"}] and turns[0]["handover"]["tasks_for_owner"] == [{"text": "量臥室"}]
    assert not turns[0]["handover"]["fallback"]  # its own handover, not the lease's last one
    assert turns[1]["handover"]["fallback"]
def test_a_conversation_is_stored_exactly_as_it_is_given():
    from muninn.adapters.store_sqlite import SqliteStore

    s = SqliteStore(":memory:")
    turn = lambda text, ts: {"role": "user", "text": text, "ts": ts}  # noqa: E731
    s.save_thread("t", {"about": None, "ts": "3", "turns": [turn("a", "1"), turn("c", "3")]})
    s.save_thread("t", {"about": None, "ts": "3", "turns": [turn("a", "1"), turn("b", "2"), turn("c", "3")]})  # one put between
    assert [x["text"] for x in s.thread("t")["turns"]] == ["a", "b", "c"]
    t = s.thread("t")
    t["turns"][0]["text"] = "a, said again"  # one changed in place
    s.save_thread("t", t)
    assert [x["text"] for x in s.thread("t")["turns"]] == ["a, said again", "b", "c"]
    s.save_thread("t", {**t, "turns": t["turns"][:1]})  # shorter
    assert [x["text"] for x in s.thread("t")["turns"]] == ["a, said again"]
