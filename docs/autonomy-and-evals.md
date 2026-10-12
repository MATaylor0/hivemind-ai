# Autonomy and evals: how a digital worker earns independence

Replacing work that people do today is safe only if each agent's independence is earned and measured. HiveMind
makes that explicit: every agent has an autonomy level, every level has an evidence bar, and the compiler enforces
the level in what the agent can actually do.

## The levels

| Level | What the agent may do | Typical use |
|---|---|---|
| **shadow** | Read and analyse; produce what it *would* do. No write tools. | New agents. Runs alongside the person doing the job today, and outputs are compared. |
| **suggest** | Recommend actions; a person decides and acts. No write tools. | Proven analysis, unproven judgement. |
| **approve** | Act, but every change needs a person's approval (MCP `require_approval: always`). Write tools that can't be approval-gated are held back. | Most production agents at first. |
| **autonomous** | Act within its tools and policies. Needs a reviewer, sampling or tool-level approval for write tools. | Narrow, high-volume, well-evaluated work. |
| **autonomous_sampled** | As autonomous, with a share of runs (`sample_rate`) reviewed by people afterwards. | The default for "replaced" roles: independence with ongoing oversight. |

```yaml
# agents/finance/ap-clerk.yaml
autonomy:
  level: approve
  approved_by: ap.lead@contoso.com
  approved_on: "2026-10-01"
  evidence: https://contoso.sharepoint.com/sites/HiveMind/pilots/ap-clerk-shadow-results.docx
evals:
  dataset: evals/ap-clerk.jsonl
```

Environments cap autonomy. `max_autonomy: approve` on production means nothing runs unsupervised there, whatever
the spec says, until the business lifts the cap.

## Evidence bar (`policies.evals.gates`)

| Level | Golden cases | Pass rate | Named approver |
|---|---|---|---|
| shadow | 0 | n/a | no |
| suggest | 5 | 70% | no |
| approve | 10 | 80% | yes |
| autonomous | 30 | 95% | yes |
| autonomous_sampled | 30 | 95% | yes, plus `sample_rate` |

`hivemind validate` enforces the case count and approver. `hivemind gate` enforces the pass rate. Tune both per
organisation in `hivemind.yaml`, and per agent with `evals.pass_rate`.

## The loop

```
          ┌──────────── golden dataset (git) ◄──────────── human corrections ◄──┐
          ▼                                                                      │
   pull request ──► CI validate ──► deploy dev ──► gate (azd ai eval) ──► deploy prod
                                                                                 │
                                         production traces ──► trace eval ───────┘
                                         (pass rate per agent, per day)
```

1. **Golden dataset.** `evals/<agent>.jsonl`, one `{"query", "expected"}` row per case. Start with real work: the
   cases the person doing the job today handles, including the awkward ones.
2. **Gate before promotion.** The deploy workflow deploys to dev, runs `hivemind gate -e dev`, and only then deploys
   to production. In Foundry environments the gate runs Foundry's evaluators (`builtin.task_adherence`,
   `builtin.intent_resolution` by default, judged by `policies.evals.judge_model`) through
   `azd ai eval run start --fail-on pass-rate=<n>`. Locally, `gate -e local` is a smoke run that checks every case
   gets an answer.
3. **Production evaluation.** Each agent also gets a trace eval over a sample of its real runs
   (`trace_lookback_hours`, `trace_sample`), so drift shows up as a falling pass rate.
4. **Corrections become cases.** When a person corrects an agent, capture it:
   ```bash
   hivemind evals add ap-clerk --query "Invoice INV-9 has a handwritten PO number" \
     --expected "Places it on hold and asks AP to confirm the PO" --note "correction by ap.lead, 12 Oct"
   ```
   The next deployment is gated on it.

## Promoting an agent

1. Collect evidence at the current level: shadow comparisons, approval rates, gate results and production trace
   evals.
2. Add golden cases until the dataset meets the next level's minimum.
3. Open a pull request that changes `autonomy.level` and sets `approved_by`, `approved_on` and `evidence`.
4. CI validates the evidence bar, dev deploys, the gate must pass, and production deploys after review.
5. If the environment has a lower `max_autonomy`, the agent runs at the cap until it is raised. Raising it is a
   separate, deliberate change.

Demotion is the same pull request in reverse, and takes effect at the next deployment. In an emergency, use
`hivemind pause <agent> -e prod`.
