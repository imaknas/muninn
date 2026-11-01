# Decision models: points that only make a judgment can switch to a model that only returns probabilities (design proposal)

Status: steps 1–3 are done (2026-10-24); steps 4–5 wait until the evaluation has numbers.

Done:
- `core/decide.py`: the Decider interface (`decide(Question) -> Verdict | None`); LLMDecider wraps the current model routing.
  Cascade and ProbabilityDecider come in step 4, when there is a second implementation.
- The job fit check (packet_fit) now goes through the Decider. Each judgment's eval case also records "the answer, the confidence, who judged" (verdict) and the time it took (ms).
- Truth: the user's answer to "Still prepare it?", and sending or holding back an automatically prepared application packet (undo cancels that one),
  are written back into that check's case. fit means "prepare it without asking"; doubt / no mean "ask first"; a doubt the user answers "yes" to counts as one question too many.
- `muninn eval --decisions`: for each decide point, how many cases, how many have the user's words as the truth, how many were right, how long each took.
  `muninn eval` also lists how long each call takes for the candidate models, compared with what was recorded originally.
- Cost is measured in time for now (how long each call takes); when a pay-per-call decision model is connected, the money gets added.

## Starting point

Of Muninn's 17 decision points, about half are really just "pick one of a few answers": which domain is this sentence, what kind of mail is this,
are these two to-dos the same one, does this job fit, is this thing done. All of these points now ask a large model to write back a piece of JSON.
That amounts to asking a model that writes essays to answer multiple-choice questions.

Another kind of model only makes judgments: give it a question and candidate answers, and it returns a probability for each answer, writing no text (for example a decision model such as Jev).
Public evaluations say: on judgments whose answer can be read directly from the text, it comes close to large models at much lower cost; on ones that need reasoning (math, code, logic) it falls behind.
A common pattern is "take its answer when it is confident, hand it to the large model when it is not."

## Design

**1. Two kinds of points in the registry** (the decision point registry gets two more fields)
- `kind: decide | generate`: decide picks one of a fixed set of answers; generate has to write content (an answer, a draft, a summary,
  choosing tools and parameters).
- `labels`: the list of answers a decide point can return (e.g. `fit | doubt | no`, or "one of the known domains, or none of them").

**2. One interface: Decider** (the core knows only this, not whose model it is)

```
decide(point, question, labels) -> Verdict | None
Verdict = {label, confidence (0–1, or unknown), by (which implementation)}
```

Implementations (one per kind; the wiring code picks one per the registry and the settings):
- **LLMDecider**: the current approach wrapped up. It goes through the existing model routing, uses the original prompt, and parses out the answer;
  a large model's own stated confidence is unreliable, so confidence is set to "unknown".
- **ProbabilityDecider**: connects to a decision model that returns only probabilities (an external service or a local model); confidence is the probability it gives.
- **Cascade(first, then, threshold)**: ask the cheap one first; if its confidence clears the threshold, take it, otherwise hand it to the next one. It is itself a
  Decider, so it can be stacked. The threshold is set during evaluation and frozen, not tweaked after going live.

**3. Confidence connects to the "action gate"**: a judgment's result already decides whether Muninn acts directly or asks you first. High confidence → as now,
"act when sure, with undo"; low confidence → ask you first. Today the fit check uses "not sure" to ask first; later it can use the probability directly.

**4. Evaluation decides who handles each point**: every decide point is compared in the evaluation on "accuracy" and "cost", with your answers and undos as the standard;
before a point switches to a decision model (or gets a layer stacked on it), run the evaluation first, check that accuracy did not drop, and only then switch that one point in the settings.
The evaluation's own "judge" is also a decide point (a 1–5 score) and can be switched the same way.

**5. Privacy follows the existing rules**: an external decision service is just another cloud provider. Under the "use the cloud for everything" setting it can be used;
in privacy mode only cloud-safe content is sent, and the rest stays local. The settings page lists who currently judges each point.

## Points worth trying first

| Suitable | Not suitable (must write content or reason) |
|---|---|
| work_check, domain_classify, window_classify, mail_triage (the classification part), packet_fit, todo_match / task_match (whether two items are the same one), the evaluation judge | route (choosing tools and parameters), answer, why, packet (draft), evening, reflect_*, task_evidence (must infer "it is done" from evidence; evaluate first) |

## Steps

1. Add `kind` and `labels` to the decision point registry (done together with the registry).
2. Decider interface + LLMDecider; move one point (packet_fit) onto it first; eval records also store "the answer, the confidence, who judged".
3. Evaluation for each decide point: accuracy and cost; it only counts once there are enough cases with your answer as the standard.
4. Connect a decision model (ProbabilityDecider), compared only in evaluation, not live.
5. Only when the evaluation shows a point is good enough, switch that point in the settings to use it or a Cascade.

Step 4 needs an account with a service (or a local decision model running on the server); you will be asked then which one to use.

## For you to decide

1. ~~Go in this direction?~~ 10/24: decided to go ahead, doing 1–3 first (done); 4–5 wait until the evaluation has numbers.
