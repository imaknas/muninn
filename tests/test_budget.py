"""A daily limit on what the cloud models cost (budget.daily_usd): counted from the tokens each reply took at the
prices config gives; once reached, no cloud model is asked until the next day, each point does what it does when no
model answers, and the user is told once that day."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.budget import DailyBudget, Price
from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter, PrivacyFirst
from muninn.core.models import Sensitivity
from muninn.core.ports import Reply
from muninn.llm_config import llm_setup
from muninn.main import IMPLS

from conftest import FakeClock, at

PRICES = {"api:small": Price(1, 5), "api:large": Price(3, 15)}


class Backend:
    def __init__(self, name: str, *, local: bool = False, took: tuple[int, int] = (1000, 100), replies=None):
        self.name, self.label, self.is_local, self.took = name, name, local, took
        self.capabilities = None
        self.replies = list(replies or [])
        self.asked = 0

    def available(self) -> bool:
        return True

    def complete(self, system, prompt, json_mode=False, **_):
        self.asked += 1
        return Reply(self.replies.pop(0) if self.replies else '{"ok": true}', *self.took)


@pytest.fixture
def day(tmp_path):
    store = SqliteStore(tmp_path / "m.db")
    clock = FakeClock(at(2026, 10, 22, 9))
    told: list[tuple[float, float]] = []
    budget = DailyBudget(0.05, PRICES, store.usage_since, clock, store.get_state, store.set_state,
                         lambda spent, limit: told.append((spent, limit)))
    return store, clock, budget, told


def kept(store, clock, backend: str, tokens: tuple[int, int], hours_ago: float = 0) -> None:
    store.add_usage(clock.now() - timedelta(hours=hours_ago), backend, *tokens)


def test_a_day_costs_what_its_priced_replies_took_since_local_midnight(day):
    store, clock, budget, _ = day
    kept(store, clock, "api:small", (10_000, 1_000))  # 0.01 + 0.005
    kept(store, clock, "api:large", (1_000, 1_000))  # 0.003 + 0.015
    kept(store, clock, "local:qwen", (50_000, 9_000))  # costs nothing: no price
    kept(store, clock, "api:large", (900_000, 900_000), hours_ago=10)  # yesterday, before local midnight
    assert budget.spent() == pytest.approx(0.033)
    assert not budget.over()


def test_reaching_the_limit_is_told_once_that_day_and_the_next_day_starts_afresh(day):
    store, clock, budget, told = day
    kept(store, clock, "api:large", (10_000, 2_000))  # 0.03 + 0.03
    assert budget.over() and budget.over()
    assert told == [(pytest.approx(0.06), 0.05)]
    clock.t = at(2026, 10, 23, 8)
    assert not budget.over()
    kept(store, clock, "api:large", (10_000, 2_000))
    assert budget.over() and len(told) == 2


def test_every_reply_counts_however_the_model_was_reached(day):
    from muninn.core.budget import Metered

    store, clock, budget, _ = day
    keep = lambda name, tokens_in, tokens_out: store.add_usage(clock.now(), name, tokens_in, tokens_out)  # noqa: E731
    judge = Metered(Backend("api:large", took=(10_000, 2_000)), keep)
    LLMRouter([judge], (), CloudFirst()).complete(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, True, "judge"))  # kept as no case
    assert store.eval_cases() == [] and budget.spent() == pytest.approx(0.06)
    Metered(Backend("api:small", took=(10_000, 0)), keep).complete("s", "an evaluation asking it directly")
    assert budget.spent() == pytest.approx(0.07)
    Metered(Backend("cli:sonnet", took=(None, None)), keep).complete("s", "a provider that says nothing")
    assert store.usage_since(clock.now() - timedelta(days=1)) == [("api:large", 10_000, 2_000), ("api:small", 10_000, 0)]


def test_tokens_kept_with_cases_before_usage_was_kept_apart_still_count(tmp_path):
    db = tmp_path / "m.db"
    store = SqliteStore(db)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    store.add_eval_case(now, "route", "s", {}, "r", "api:large", by_local=False, cloud_ok=True, json_mode=True, tokens=(10_000, 2_000))
    store.add_eval_case(now - timedelta(days=5), "route", "s", {}, "r", "api:large", by_local=False, cloud_ok=True, json_mode=True,
                        tokens=(9, 9))
    store._db.execute("DELETE FROM usage")  # as an older version left it
    assert SqliteStore(db).usage_since(now - timedelta(hours=1)) == [("api:large", 10_000, 2_000)]
    assert SqliteStore(db).usage_since(now - timedelta(days=9)) == [("api:large", 10_000, 2_000)]  # once, and only recent ones


def test_over_the_limit_no_cloud_model_is_asked_and_the_request_is_refused_as_such():
    cloud = Backend("api:small")
    router = LLMRouter([cloud], (), CloudFirst(), over_budget=lambda: True)
    c = router.complete_case(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="route"))
    assert c.unanswered and c.refused == "budget" and cloud.asked == 0
    assert LLMRouter([cloud], (), CloudFirst(), over_budget=lambda: False).complete(
        LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="route")) == '{"ok": true}'


def test_over_the_limit_a_local_model_answers_what_the_cloud_would_have():
    from muninn.core.llm_router import _make_cloud_safe

    cloud, local = Backend("api:small"), Backend("local:qwen", local=True)
    router = LLMRouter([local, cloud], ("work",), PrivacyFirst(), over_budget=lambda: True)
    c = router.complete_case(LLMRequest("s", _make_cloud_safe("{}"), Sensitivity.PERSONAL, "garden", task="evening"))
    assert (c.backend, c.stood_in, cloud.asked) == ("local:qwen", True, 0)


def test_what_only_a_local_model_may_read_costs_nothing_and_asks_no_budget():
    cloud, local = Backend("api:small"), Backend("local:qwen", local=True)
    checked = []
    router = LLMRouter([local, cloud], (), PrivacyFirst(), over_budget=lambda: checked.append(1) or True)
    c = router.complete_case(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="facts"))
    assert c.backend == "local:qwen" and cloud.asked == 0
    assert checked == []


def test_a_reply_asked_for_twice_costs_both():
    cloud = Backend("api:small", took=(1000, 100), replies=['{"wrong": 1}', '{"ok": true}'])
    recorded = []
    router = LLMRouter([cloud], (), CloudFirst(), lambda req, reply, backend, **kw: recorded.append(reply),
                       check_reply=lambda task, reply: [] if "ok" in reply else ["ok: required"])
    assert router.complete(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, json_mode=True, task="route")) == '{"ok": true}'
    assert (recorded[0].input_tokens, recorded[0].output_tokens) == (2000, 200)


def test_prices_are_read_by_backend_name_and_a_wrong_one_is_said():
    llm = {"providers": {"api": {"impl": "anthropic", "key": {"env": "INVENTED_KEY"},
                                 "prices": {"claude-haiku-5-5": {"input": 1, "output": 5}}}}}
    assert llm_setup(llm, IMPLS, set()).prices() == {"api:claude-haiku-5-5": Price(1.0, 5.0)}
    llm["providers"]["api"]["prices"] = {"claude-haiku-5-5": 3}
    with pytest.raises(ValueError, match=r"llm.providers.api.prices.claude-haiku-5-5"):
        llm_setup(llm, IMPLS, set())


def built(tmp_path, yaml: str):
    from muninn.config import Config
    from muninn.main import build

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text("language: zh-TW\n" + yaml)
    return build(Config.load(tmp_path), dry_run=True)


API = ("llm:\n  routing: cloud\n  providers:\n    api: {impl: anthropic, key: {env: INVENTED_KEY},\n"
       "      prices: {claude-haiku-5-5: {input: 1, output: 5}, claude-sonnet-5-5: {input: 3, output: 15}}}\n")


def test_a_limit_that_could_not_count_what_it_limits_is_a_problem(tmp_path):
    assert built(tmp_path / "a", "budget: {daily_usd: 2}\n" + API).problems == []
    unpriced = built(tmp_path / "b", "budget: {daily_usd: 2}\n" + API.replace(", claude-sonnet-5-5: {input: 3, output: 15}", ""))
    assert unpriced.problems == ["設了 budget.daily_usd,但 api:claude-sonnet-5-5 沒有價格,花費算不出來:加上 "
                                 "llm.providers.api.prices.claude-sonnet-5-5,寫成 {input: <美元>, output: <美元>}(每百萬 token)。"
                                 "在那之前,需要模型的指令都不會跑"]
    cli = built(tmp_path / "c", "budget: {daily_usd: 2}\nllm:\n  routing: cloud\n  providers:\n    claude: {impl: claude, binary: /bin/echo}\n")
    assert all("不會說用了多少 token" in p for p in cli.problems) and cli.problems
    assert "大於 0" in built(tmp_path / "d", "budget: {daily_usd: free}\n" + API).problems[0]
    assert built(tmp_path / "e", API).problems == []  # no limit: nothing to count


def test_once_the_day_is_spent_what_the_user_says_is_kept_and_they_are_told_once(tmp_path, monkeypatch):
    from muninn.adapters.llm.anthropic_api import AnthropicApiBackend

    monkeypatch.setattr(AnthropicApiBackend, "complete",
                        lambda self, system, prompt, json_mode=False: Reply(json.dumps({"actions": [], "reply": "好"}), 2_000, 200))
    app = built(tmp_path, "budget: {daily_usd: 0.02}\n" + API)
    e = app.engine
    said = [e.command(t).reply for t in ("記一下買牛奶", "還有買蛋", "再加麵包", "還有果醬", "跟優格", "最後是咖啡豆")]
    over = "今天的模型預算用完了,這句先記成筆記;明天(或調高 budget.daily_usd 後)我就聽得懂了。"
    assert said[0] == "好" and said[-1] == over  # each sentence costs about a cent: answered, then not
    assert set(said[said.index(over):]) == {over}  # not asked again that day
    assert e.store.get_state("budget:said") == e.clock.now().date().isoformat()
