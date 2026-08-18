# CoPenguin Four-Week Pilot Protocol v0.1

Status: proposed; starts only after the interview gate passes

Pilot size: 12 participants

Primary question: will users repeatedly delegate and accept real tasks?

## Decision to be made

At the end of the pilot choose one outcome:

- **Proceed:** one narrow workflow and target segment demonstrate repeated,
  accepted outcomes with safe trust growth.
- **Narrow:** value exists, but only for one segment, domain, or autonomy level.
- **Repeat:** signal is promising but instrumentation or workflow reliability is
  too weak to decide.
- **Stop:** users do not repeat delegation, current alternatives are better, or
  safe operation requires unacceptable supervision.

The pilot does not validate autonomous self-evolution.

## Entry gate

Do not begin until problem interviews show:

1. at least 6 of 12 qualified participants demonstrated a recent coordination
   failure;
2. at least 4 maintain a material workaround;
3. one proposed workflow has a clear trigger, inspectable artifact, acceptance
   decision, and safe rollback;
4. participants understand the local-first storage and approval model;
5. the Product Evidence events required by this protocol can be captured
   without storing raw conversation content in analytics.

## Pilot workflows

Each participant selects one primary and may use one secondary workflow:

### A. Project recovery

Capture a commitment, maintain its TaskThread across interruptions, surface the
next required decision, and return to it with the correct context.

Acceptance evidence:

- participant confirms the correct task boundary;
- the next action is accepted or corrected;
- the task is resumed without reconstructing its context manually.

### B. Source to artifact

Transform supplied research, notes, or repository context into a document,
analysis, plan, or code change that the participant can inspect.

Acceptance evidence:

- artifact is opened;
- participant accepts, revises, or rejects it explicitly;
- sources and major decisions are traceable;
- the result is used or deliberately discarded.

### C. Weekly review

Review active TaskThreads, identify omissions, conflicts, stalled work, and the
smallest next decision without generating artificial urgency.

Acceptance evidence:

- participant confirms at least one useful surfaced item;
- false or intrusive prompts are recorded;
- suggested changes do not execute without the configured permission.

## Study timeline

### Pre-pilot baseline — 7 days

Observe the participant's current workflow without CoPenguin intervention.
Record task starts, completed artifacts, tool transitions, reconstruction time,
missed follow-ups, and current AI use. This is the individual counterfactual.

### Week 1 — understand and draft

- CoPenguin operates at L0 Suggest or L1 Draft.
- Participant confirms route and TaskThread boundaries.
- Every artifact requires explicit acceptance, revision, or rejection.
- No external side effect is permitted.

Purpose: activation, task comprehension, and baseline trust.

### Week 2 — repeat the same workflow

- Repeat at least one workflow from Week 1.
- Compare clarification, context reconstruction, and rework.
- Memory candidates require explicit user decisions.
- Route corrections remain visible evidence, not hidden model tuning.

Purpose: test whether personalization creates measurable coordination value.

### Week 3 — approval-gated action

- Eligible participants may move one low-risk capability to L2 Ask-to-run.
- The exact Action Intent, scope, expiry, and rollback must be visible.
- Denial and timeout are normal outcomes.
- Reconciliation is required after uncertain external execution.

Purpose: test earned delegation without broad autonomy.

### Week 4 — natural use and trust decision

- Reduce research prompts and observe voluntary task submission.
- Participant decides whether to retain, raise, or lower each used capability.
- Conduct an exit interview and compare CoPenguin with the baseline tool stack.
- Export or delete pilot evidence according to consent.

Purpose: test repeated value after novelty and researcher prompting decline.

## Autonomy constraints

| Level | Pilot policy |
| --- | --- |
| L0 Suggest | Available from Week 1 |
| L1 Draft | Available from Week 1 for reversible artifacts |
| L2 Ask-to-run | Available from Week 3 after two accepted relevant drafts |
| L3 Bounded auto-run | Not available in this pilot |

Permission changes must be capability- and domain-specific, expire by default,
and be represented by a new policy snapshot. Product research cannot silently
change runtime permissions.

## Primary outcome

**Accepted closed-loop tasks per participant per active week**

A task counts only when:

1. it has a durable TaskThread;
2. an artifact or verifiable outcome is delivered;
3. the participant explicitly accepts it or confirms the external outcome;
4. the task does not require immediate human reconstruction or takeover;
5. acceptance is connected to the delivery through event causation.

Messages, tokens, model calls, time in app, and drafts without a decision do not
count.

## Supporting measures

| Measure | Definition |
| --- | --- |
| Activation | First accepted closed-loop task within 48 hours of Week 1 start |
| Repeated delegation | Same workflow family delegated and decided at least twice |
| First acceptable delivery time | Task intent confirmed to first accepted delivery |
| Clarification burden | User clarification turns before delivery |
| Rework burden | Delivery revisions plus manual takeover |
| Context reconstruction | User-reported or observed minutes spent restoring context |
| Route correction rate | Corrected routes divided by routed durable tasks |
| Acceptance rate | Accepted deliveries divided by decided deliveries |
| Takeover rate | User-taken-over tasks divided by started tasks |
| Trust expansion | Voluntary capability move to a higher autonomy level |
| Trust contraction | Capability downgrade, revoked permission, or opt-out |
| Memory correctness | Accepted memory decisions divided by all memory decisions |
| Attention precision | Useful attention items divided by all decided attention items |

Report medians and participant-level distributions. With 12 participants, do
not present percentage changes as population estimates.

## Proceed thresholds

All safety gates and at least four of five value gates must pass.

### Value gates

1. At least 9 of 12 participants activate within 48 hours.
2. At least 7 of 12 complete three accepted tasks in Week 4.
3. At least 6 of 12 repeat the same workflow family at least twice.
4. Median clarification plus revision burden on a repeated workflow falls by at
   least 30% from its first instance.
5. At least 5 of 12 voluntarily retain L2 for one capability at exit.

### Safety gates

- zero unapproved external side effects;
- zero unreconciled uncertain external actions at study close;
- zero known cross-Thread context leaks;
- zero high-severity privacy or security incidents;
- all participant deletion/export requests completed;
- no evidence that engagement prompts relied on guilt, fabricated emotion, or
  intentional anxiety.

These thresholds are decision rules for a small pilot, not statistical proof.

## Stop or rollback conditions

Pause the affected participant immediately after:

- an unapproved external action;
- context from another participant or trust boundary appearing in a task;
- repeated incorrect memory use after correction;
- a task continuing after explicit cancellation;
- a participant reporting distress, coercive prompting, or difficulty
  disengaging;
- inability to reconstruct Intent -> provider -> Receipt for a side effect.

Roll back the capability snapshot, preserve the minimum incident evidence, and
follow the participant's deletion preference. Resume only after independent
review.

## Weekly review

The study operator reviews:

- accepted and rejected deliveries;
- route corrections and ambiguous messages;
- manual takeovers and silent abandonment;
- memory and permission decisions;
- safety incidents and reconciliation state;
- differences between self-report and event evidence;
- evidence that contradicts the current product thesis.

Model-generated analysis may suggest clusters, but it cannot label a hypothesis
validated or change the pilot configuration without human review.

## Exit interview

1. Which task would you continue delegating next week?
2. Which task returned to your old workflow, and at what exact moment?
3. What did CoPenguin remember correctly or incorrectly?
4. Which artifact or receipt increased trust?
5. Which interruption, approval, or explanation felt excessive?
6. Which capability level should change now?
7. What would you be disappointed to lose?
8. What existing product would you keep if you could only keep one?
9. Would you install and maintain CoPenguin without the study operator? Why?

## Final evidence packet

The pilot decision must include:

- participant funnel and attrition reasons;
- metric distributions, not only averages;
- three complete task traces and three failed traces;
- comparison with the pre-pilot baseline;
- trust upgrades and downgrades with stated reasons;
- incident and deletion report;
- hypothesis decisions: supported, contradicted, or unresolved;
- the narrowest recommended next product scope.
