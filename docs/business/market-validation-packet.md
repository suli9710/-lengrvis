# Market Validation Packet

Status: hypothesis and fieldwork plan only. This document does not record completed interviews, approved public claims, or paid-launch readiness.

## Target Segment Hypothesis

Primary wedge to test:

Small Windows-heavy professional services teams that handle confidential client documents and cannot casually send source files to cloud AI tools.

Initial interview focus:

- 3-30 person law firms, accounting firms, bookkeeping offices, and compliance-heavy consultants.
- Daily operators: partners/owners, associates, paralegals, accountants, auditors, and office managers who personally do document-heavy work.
- Workflows: local file search, contract or report summarization, evidence/binder preparation, client-data extraction, meeting-note cleanup, and repetitive desktop actions across folders, Office/PDF tools, browsers, and email.

Why this segment is testable:

- It narrows the broad "privacy-sensitive professionals" thesis from `docs/business/target-segment.md`.
- It matches the Windows-first product direction.
- It has concrete local-file workflows that can be discussed in interviews without claiming the product has already solved them.
- It can be reached through small professional networks, local service providers, industry communities, and warm referrals.

Current readiness implication:

- `docs/business/market-readiness.md` should keep `Target market = TBD` until interview evidence is collected and reviewed.
- No paid launch, public pricing claim, or "validated segment" claim is allowed from this packet alone.

## Interview Plan

Minimum: 5 completed discovery interviews.

Preferred: 8 interviews before updating readiness language.

Mix:

- 2-3 lawyers, paralegals, or law-office operators.
- 2-3 accountants, auditors, bookkeepers, or finance-office operators.
- 1-2 small-team owners or partners who approve software spend.
- 1-2 IT/service providers who support small professional offices.

Format:

- 30-45 minutes each.
- Discovery first; show product screenshots or prototype only after understanding current workflows.
- Record role, firm size, Windows usage, current tools, top workflows, blockers, and willingness signals.
- Store anonymized notes and artifacts separately from this packet before using them as readiness evidence.

## Screening Criteria

Include candidates who match at least 5 of 7:

- Works in a small professional services team or supports several such teams.
- Uses Windows as the primary work environment.
- Handles confidential client, patient, legal, financial, research, or audit material.
- Personally spends time searching, summarizing, organizing, or moving information across local files and desktop apps.
- Has tried, banned, restricted, or seriously considered AI tools for document work.
- Can describe at least one repeated workflow with real frequency and business consequence.
- Can influence, recommend, or approve a software purchase.

Exclude candidates when:

- They mainly want general cloud chatbot quality and do not care where data is processed.
- They are enterprise buyers with long procurement cycles.
- They are pure hobbyist, entertainment, or experimentation users.
- Their main need is mobile-only or web-only.
- They cannot discuss workflow details even at an anonymized level.

## Interview Script

Opening:

- "I am testing whether a local Windows assistant for confidential professional work is worth building and selling. This is not a sales call, and the segment is not validated yet."
- "Please avoid sharing client names, private data, or regulated details."

Context:

- What is your role, team size, and main operating system?
- What kinds of documents or local files consume the most time each week?
- Which desktop apps, folders, browser tools, and templates do you touch in a normal workflow?

Problem:

- Walk me through a recent task that felt repetitive, risky, or slow.
- How often does that happen, and how much time does it take?
- What happens if the task is delayed or done incorrectly?
- Who else is affected when this work piles up?

Current alternatives:

- What do you use today: manual process, scripts, search, Office features, cloud AI, vendor tools, outsourcing?
- What works well enough?
- What is still painful?
- Have privacy, client rules, firm policy, or compliance concerns blocked AI usage?

Local/privacy sensitivity:

- When would sending documents to a cloud AI tool be unacceptable?
- Is "local-first / data stays on your machine" a must-have, a nice-to-have, or irrelevant?
- What proof would you need before trusting that claim?
- Would audit logs, approvals, previews, or rollback make the tool more usable or just more complex?

Buying and pricing:

- Who would approve a tool like this?
- What budget category would it fall under?
- What price range would feel plausible for one user for one year, assuming the tool solved a real workflow?
- What would make you refuse to pay even if the demo looked good?

Concept test:

- "Imagine a Windows assistant that can search local files, summarize selected documents, propose desktop actions, ask for approval before risky operations, and keep an audit trail. Which part sounds useful, risky, or unnecessary?"
- Which single workflow should it solve first for you?
- Would you agree to a follow-up usability test or pilot when no payment is required?

Close:

- What did I misunderstand about your work?
- Who else should I speak with?
- May I follow up with a short workflow summary for correction?

## Decision Thresholds

Move from `TBD` to "candidate segment: small confidential professional services teams" only if all are true:

- At least 5 completed interviews match the screening criteria.
- At least 4 of 5 report 3+ hours/week in repeated local-document or desktop workflow pain.
- At least 4 of 5 rank privacy/local processing or auditability as a top-three buying factor.
- At least 3 of 5 name one concrete first workflow they would test with non-sensitive or anonymized data.
- At least 3 of 5 agree to a follow-up workflow review, usability test, or unpaid pilot.
- At least 2 candidates with budget influence describe a plausible paid path after a successful pilot.

Expand to 8-10 interviews before treating the segment as stronger than "candidate" if:

- Law and accounting responses diverge sharply.
- Buyers care more about cloud model quality than local control.
- The first workflow differs so much that product focus becomes unclear.

Do not advance the segment if any of these are true:

- Fewer than 5 qualified interviews are completed.
- Local processing is mostly "nice to have" rather than a meaningful constraint.
- The main requested workflows require legal, medical, tax, or compliance advice from the model rather than document handling and operator-approved actions.
- Prospects will not discuss repeat workflows, pilot conditions, or buying authority.
- Interest depends on prohibited public claims such as guaranteed compliance, zero risk, or approved legal/security certification.

## Market-Readiness Update Path

After interviews, create a reviewed evidence artifact or appendix with anonymized rows:

- Interview ID, date, role, segment, team size, Windows usage.
- Qualified yes/no and screening rationale.
- Top workflow, current alternative, weekly time cost, consequence.
- Local/privacy/audit importance.
- Buying authority and price signal.
- Follow-up commitment.
- Disqualifying concerns.

If thresholds pass:

- Update `docs/business/market-readiness.md` current decision field from `Target market = TBD` to a candidate-market label, not a paid-launch pass.
- Keep every `MR-P0` row unchanged unless its required evidence is independently reviewed and passed.
- Update `docs/business/target-segment.md` with the validated/candidate segment wording and link to the evidence artifact.
- Update `docs/business/public-claims-register.md` only with claims that are supported by evidence and still comply with paid-launch blockers.

If thresholds fail:

- Leave `Target market = TBD`.
- Record the failed hypothesis, strongest negative signals, and next segment to test.
- Do not use failed or inconclusive interviews as public launch evidence.

