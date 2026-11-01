# Decision point registry (design proposal)

Status: done (2026-10-24): contracts/llm-points.yaml, contracts/llm/*.schema.json, tests/test_llm_points.py, `muninn eval --coverage`. The format is shared with another system (contracts/vendor/records/llm-points.v1.schema.json), and the tests validate against it.

## The problem

Muninn has 17 places where it asks a model to make a judgment (table below). Each carries a name in the code (`LLMRequest.task`), but there is no
list saying what each point judges, what its output looks like, what happens when the model fails, and how to tell whether it judged right. As a result:

- **Evaluation (eval) has no foundation**: to switch models (for example, to evaluate whether a local model can take over), you first need to know which points exist, how each is scored,
  and where the truth comes from. Right now only 7 of the 17 points have a dedicated scoring method; the rest rely on "model as judge".
- **Failure behavior is scattered**: some points skip on failure, some retry next time, some use a default. There is no place to see this at a glance,
  and no test making sure "a failure does not quietly turn into a wrong result".
- **Written separately from the other system**: the system that manages projects already has its own decision point registry (checked nightly). If the formats match,
  both sides can use the same tools for evaluation, model choice and checks.

## Proposal: one registry file, plus tests that block

`contracts/llm-points.yaml`, one entry per decision point:

```yaml
v: 1
points:
  packet_fit:
    caller: core/prepare.py            # who sends the request
    purpose: Before preparing an application packet, judge whether the job fits (basic requirements, the user's requirements)
    model_group: draft                 # which model group (a key of llm.models, see core/task_models.py)
    material: personal                 # what the content is: cloud_safe | personal | sensitive | local_only
    output: {format: json, schema: llm/packet_fit.schema.json}
    on_failure: retry_later            # what to do when the model does not answer or its output is invalid (see below)
    eval:
      scorer: field:verdict            # how to score; judge = ask a model to act as judge
      truth: the user's answer to "Still prepare it?"; saying "not applying" to an automatically prepared one   # where the truth comes from
```

**Fields aligned with the other system**: `caller`, `purpose`, `output` (schema), `on_failure`.
**Muninn's own additions**: `model_group` (Muninn picks models by group, not by environment variables), `material` (decides whether it may go to the cloud;
under the "use the cloud for everything" setting everything goes to the cloud; it only makes a difference in privacy mode), `eval` (the scoring method and where the truth comes from).
**What Muninn does not need**: permissions, tool lists (Muninn's model calls are plain text in and out; the model is given no tools).

**`on_failure` has only five values (both sides share the same set), and every point must pick one** (there is no "quietly use something else" option):
- `skip`: do not do it this time, and leave no result (e.g. if the evening summary cannot be written, it is not written).
- `retry_later`: do not advance progress; try again next round (e.g. the fit check, task matching).
- `ask_user`: ask the user instead (e.g. a window whose domain cannot be judged).
- `say_so`: tell the user directly that the model did not answer (e.g. a reply in the conversation).
- `default`: use the default written in the registry, and it must be the conservative side (e.g. if it cannot judge whether something is work, treat it as work and keep it local).

**Tests that block** (placed in the existing architecture tests):
1. Every `LLMRequest` task in the code is in the registry; every point in the registry is used in the code (no more, no less).
2. Each point's `model_group` is a group in `task_models.py`, or explicitly `default`.
3. Each `output.schema` file exists, and that point's parse function can parse the schema's example.
4. Every point has `on_failure`, and a test can make that point's model fail and verify the behavior really is the declared one.
5. Every point has `eval.scorer` (`judge` if there is no dedicated scoring) and `eval.truth`.

**Shared format**: the registry's format (JSON Schema) lives in the other system's contracts; Muninn vendors a copy through its current contract process.
Both sides check against it nightly. The contents of each side's registry are not shared, only the format.

## The current 17 decision points

| Point | What it judges | Model group | On failure now | Scoring |
|---|---|---|---|---|
| route | what the user's words should do (choose the tool and parameters) | route | says the model did not answer | yes |
| facts | which to-dos the news the user mentions affects | facts | skips the matching | yes |
| answer | answer the user's question | answer | says the model did not answer | judge |
| why | why a plan item exists | answer | says the model did not answer | judge |
| work_check | which domain this sentence belongs to (is it work) | — | treats it as work (kept local) | yes |
| domain_classify | which domain a record goes into | — | leaves it unclassified | yes |
| window_classify | which domain a window counts toward | — | asks the user | yes |
| mail_triage | the category of a new mail, plus a reply draft for ones that need a reply | mail | no category, no draft | yes (category) |
| packet | application packet draft | draft | retries next round | judge |
| packet_fit | whether the job fits | draft | retries next round | yes (the user's answer is the truth) |
| todo_match | whether a project to-do and a task are the same one | — | retries next round | yes |
| task_match | whether a new task is a duplicate or already done | — | retries next round | to be added |
| task_evidence | whether there is evidence that a task is done | — | does not close it | to be added |
| evening | evening summary | — | does not write it | judge |
| reflect_digest | summary of one conversation | reflect | skips | judge |
| reflect_week | weekly reflection | reflect | skips | judge |
| turn_topic | which conversation a sentence belongs to | — | continues the conversation on screen | judge |
| topic_state | the current state of a topic in a long conversation | — | keeps the previous version; the original text not yet merged in is read as is | judge |
| topic_recall | which original sentences in a long conversation to go back and read | — | fetches none | judge |
| idea_next | when an experiment is handed back, whether to ask about upgrading it to a project (the next step during a discussion is route's next) | — | does not ask | judge |
| judge | acts as the judge in evaluation | — | that case is not scored | — |

("—" means the routing's default cloud model; the registry will write it out explicitly. "On failure now" was compiled from reading the code; each one is confirmed with a test when it is registered.)

## Relation to evaluation

This registry is the evaluation's checklist: `muninn eval` changes to run from the registry. Each point uses its declared scoring method, with the "source of truth"
(the user's later answers, undos, edits) as the standard; when there is no answer from the user, the cloud model's answer serves as the reference answer to compare the local model against.
A mature evaluation means: every point has a scoring method and enough cases with a truth, so one run before switching models tells you whether the switch works.

## For you to decide

1. Go with this? (registry file + five tests + the 3 missing scorers added + `muninn eval` runs from the registry)
2. Share the format with the other system (they have to agree too; I will send them a mail)?

## Found while building it (reading the code point by point to confirm the failure behavior)

- task_evidence: when the model returned something unreadable, it used to move the evidence progress forward, so that piece of evidence was never looked at again. Fixed: it is looked at again next time.
- todo_match / task_match: nothing guarded against a reply that is not an object. Guard added (in practice this almost never happens).
- mail_triage: one mail that can never be parsed blocks every later mail in that mailbox (not fixed yet).
- The current state of evaluation (`muninn eval --coverage`): there are many recorded cases, but few carry the user's own answer (the truth);
  a few points have none at all. For evaluation to mature, more judgments first need to carry the user's answer.
