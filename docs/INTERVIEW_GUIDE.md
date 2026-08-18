# CoPenguin Problem Interview Guide v0.1

Status: ready for recruitment

Study size: 12-15 interviews

Session length: 55-65 minutes

## Research objective

Determine whether AI-native multi-project workers experience a frequent,
costly, and currently unsolved gap between receiving a commitment and delivering
an accepted result. The interview is not a sales call and must not attempt to
prove that users want a “super agent.”

## Recruitment matrix

Recruit by observed working behavior rather than age or job title.

| Cohort | Target | Example roles |
| --- | ---: | --- |
| Builds and ships | 4-5 | independent developer, founder, technical creator |
| Synthesizes and decides | 4-5 | product manager, researcher, consultant |
| Coordinates and delivers | 4-5 | operator, freelancer, client-service creator |

Required screener:

1. Uses a general AI assistant at least four days per week.
2. Maintains at least three simultaneous projects or commitments.
3. Uses at least three relevant tools: AI chat, notes, tasks/calendar,
   messaging, email, development, or automation.
4. Can show a real multi-step task completed in the previous two weeks.
5. Is willing to discuss mistakes and workarounds, not only successful demos.

Exclude candidates whose primary need is emotional companionship, whose work
cannot be safely observed even in redacted form, or who expect autonomous
high-risk medical, legal, financial, or public actions.

## Interview principles

- Ask about the last real occurrence, not an imagined future.
- Request timestamps, artifacts, and tool transitions where safe.
- Do not introduce CoPenguin features until the existing workflow is mapped.
- Treat “I would use that” as weak evidence.
- Treat a shown workaround, repeated cost, paid alternative, or abandoned task
  as stronger evidence.
- Separate a missing feature from reliability, setup, permission, and trust
  problems.
- Never collect secrets, customer data, credentials, or unredacted sensitive
  content.

## Session script

### 1. Opening and consent — 5 minutes

Suggested introduction:

> We are studying how people manage work that begins in messages or AI chats
> and later needs to become a finished result. We are evaluating the problem,
> not your productivity. With your permission, I will take notes about the
> workflow and tool transitions. Please hide or redact anything sensitive. You
> can skip any question or ask for your notes to be deleted.

Record:

- consent to notes;
- consent to anonymized aggregate analysis;
- separate consent for screenshots or redacted artifacts;
- requested retention period and deletion contact.

### 2. Work landscape — 8 minutes

1. What active work and personal projects are competing for your attention this
   week?
2. Where do new requests normally arrive?
3. Which system do you trust to tell you that something is still unfinished?
4. What do you use ChatGPT, Claude, Gemini, or another assistant for repeatedly?
5. Which tasks do you deliberately keep away from AI, and why?

Do not ask whether they want one Inbox or a personal agent.

### 3. Recent-task walkthrough — 20 minutes

Ask the participant to choose a task from the previous two weeks that crossed at
least two tools and produced an artifact or external outcome.

Prompts:

1. What first caused this task to exist?
2. Show where the original request or idea arrived.
3. At what moment did it become a commitment rather than a conversation?
4. How did you preserve the context?
5. What other tasks interrupted it?
6. How did you remember the next step?
7. What did AI do, and what did you still have to copy, check, or redo?
8. Who or what decided that the result was acceptable?
9. Where is the final artifact now?
10. What evidence would you need before allowing software to perform the final
    action?
11. If this task failed silently, when would you notice?

Map the actual chain:

```text
trigger -> capture -> classify -> context -> work -> interruption -> recovery
        -> draft -> verification -> external action -> acceptance -> follow-up
```

For every transition record the tool, human effort, wait time, ambiguity, error,
and workaround.

### 4. Failure and switching moments — 10 minutes

1. Tell me about the last task that was forgotten, duplicated, or resumed with
   the wrong context.
2. What did that failure cost: time, money, confidence, relationship, or missed
   opportunity?
3. When has an AI answer looked good but been unusable as the final result?
4. Have you stopped using an assistant, automation, or task system? What exact
   incident caused the change?
5. What setup or maintenance work makes current automation not worth it?
6. Have you ever withheld data or permission because you could not see how it
   would be stored or used?

### 5. Trust and delegation ladder — 8 minutes

Use one real workflow from the walkthrough. Ask the participant to place each
stage on the highest level they would accept today:

| Level | Meaning |
| --- | --- |
| L0 Suggest | Recommend the next action only |
| L1 Draft | Create an artifact without external effect |
| L2 Ask-to-run | Prepare the external action and require approval |
| L3 Bounded auto-run | Execute only inside an explicit scope and budget |

Follow-up questions:

- What evidence would move it up one level?
- What single failure would move it down?
- Which permission should expire automatically?
- What must be reversible?
- What memory would be helpful, and what would feel invasive?

### 6. Concept reaction — 5 minutes

Only now present this neutral concept:

> One Inbox accepts ordinary messages. Work that becomes a commitment receives
> its own persistent TaskThread, status, artifacts, and history. The assistant
> may prepare or execute bounded actions, but memory and permission changes are
> inspectable and high-impact actions require approval.

Ask:

1. Which part maps to a problem you showed today?
2. Which part is unnecessary?
3. What would you continue using instead?
4. What task would you test first with your own data?
5. What would prevent you from trying it this month?

Do not ask “Would you pay?” without first locating an existing spend, labor
cost, or switching cost.

### 7. Close — 4 minutes

- Ask permission for a four-week pilot.
- Confirm the safest first workflow.
- Ask which existing tool is the true comparison baseline.
- Reconfirm deletion or redaction requests.

## Interview evidence record

Create one anonymized record per participant:

| Field | Recording rule |
| --- | --- |
| Participant ID | Random study ID; no name in analysis dataset |
| Segment evidence | Behaviors satisfying the screener |
| Repeated workflow | Specific trigger, frequency, and output |
| Existing stack | Tools actually shown or named |
| Failure event | Recent concrete example |
| Cost | Time, money, risk, or qualitative impact |
| Workaround | Existing behavior or paid product |
| Trust ceiling | Current autonomy level by capability |
| Upgrade evidence | Evidence needed for one higher level |
| Disconfirming evidence | Reasons CoPenguin is unnecessary or worse |
| Pilot candidate | Yes/no plus safest workflow |

Store content or screenshots only as consented Artifact references. Research
analysis should use coded facts and short redacted notes, not raw transcripts by
default.

## Synthesis rubric

Score each problem from 0-3 on:

- **Frequency:** rare to weekly/daily;
- **Severity:** annoyance to material missed outcome;
- **Workaround commitment:** none to paid/maintained system;
- **CoPenguin leverage:** generic answer to durable cross-tool closure;
- **Confidence:** claimed future to directly demonstrated recent event.

A problem moves to pilot only when:

- at least 6 of 12 qualified participants show a recent instance;
- at least 4 already maintain a meaningful workaround;
- the problem occurs in at least two recruitment cohorts;
- there is at least one safe, repeatable workflow with an inspectable outcome;
- contradictory evidence and excluded segments are documented.

## Anti-patterns

Do not count these as validation:

- enthusiasm for “Jarvis” or a super-agent concept;
- willingness to join a waitlist;
- number of features requested;
- time spent chatting with the prototype;
- a one-off spectacular task;
- praise from users who did not supply a real workflow;
- founder or team members serving as the majority of participants.
