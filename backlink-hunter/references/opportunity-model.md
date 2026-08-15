# Backlink Opportunity Model

Use one durable record per distinct combination of source page or organization, intended target page, and acquisition method. Preserve immutable attempt history; update the current opportunity status instead of creating duplicates.

## Contents

- [Required record](#required-record)
- [Categories worth exploring](#categories-worth-exploring)
- [Competitor research](#competitor-research)
- [Score consistently](#score-consistently)
- [Status model](#status-model)
- [Attempt history](#attempt-history)
- [Verification standard](#verification-standard)

## Required record

Store at least:

- stable opportunity ID;
- discovered time and last-updated time;
- opportunity domain and canonical source URL, when known;
- intended target URL on the authorized domain;
- category and acquisition method;
- discovery source, competitor/source evidence, and why the link would help real users;
- contact route and whether it is public and appropriate;
- relevance, site quality, audience value, viability, cost, difficulty, risk, and total score;
- estimated monetary cost (`free`, an amount/range, or `unknown`), never guessed as zero;
- current status, next action, owner/agent, and next-check time;
- attempt history, messages, replies, registrations, submissions, and outcomes;
- verification fields when a live link exists;
- rejection, decline, suppression, or risk rationale when applicable.

Do not store passwords, authentication codes, cookies, API keys, sensitive personal data, or full unrelated inbox messages.

## Categories worth exploring

Prioritize sources that have a genuine reason to reference the target:

- unlinked brand, product, research, person, or organization mentions;
- competitor link gaps where the target offers equal or better relevant value;
- broken/outdated resource replacement, with a genuinely suitable target page;
- curated resource pages and reading/tool lists;
- reputable industry, professional, nonprofit, educational, local, or niche directories;
- chambers of commerce, trade groups, associations, certification bodies, clubs, and community organizations the target genuinely qualifies for;
- supplier, distributor, partner, customer, alumni, portfolio, integration, or member listings based on a real relationship;
- local citations and yellow-page-style indexes with editorial standards and real users;
- original research, data, tools, guides, case studies, and other linkable assets;
- journalist or expert-source requests that fit real expertise;
- legitimate sponsorships or advertisements when separately approved and correctly qualified as sponsored/nofollow;
- historical links, moved pages, or expired resources discovered through web archives, used only as lead evidence for a current live placement.

Do not pursue low-value bulk directories, spun guest posts, press-release syndication for ranking credit, comment/forum/profile spam, private blog networks, sitewide template links, fake organizations, link insertion marketplaces, or sites built mainly to sell ranking links.

## Competitor research

Select a small, defensible competitor set: organizations that compete for the same audience, service, product, topic, or local results. Record why each is a competitor. Do not assume the largest search result is a business competitor.

For each competitor:

1. collect referring domains and source pages from the best available SEO index/export;
2. find domains linking to multiple competitors but not the target;
3. inspect the actual live source page and placement context;
4. infer the legitimate acquisition path rather than copying anchor text or tactics blindly;
5. reject sources that are irrelevant, paid-for-ranking, automated, compromised, or low quality;
6. record the competitor and source evidence in the opportunity.

## Score consistently

Score each dimension from 0 to 5 using evidence, not intuition:

- **Relevance:** topical, geographic, professional, or audience fit.
- **Site quality:** editorial care, real identity, useful content, maintenance, and trust signals. Third-party authority metrics are supporting evidence only.
- **Audience value:** likelihood of qualified referral traffic or useful discovery by real people.
- **Viability:** clear legitimate path, reachable owner/editor, demonstrated listing policy, or comparable links.
- **Target fit:** strength of the exact page/resource proposed as the destination.
- **Cost efficiency:** 5 for a strong free/earned path; lower as time or approved monetary cost rises.
- **Difficulty:** 0 easy to 5 extremely difficult.
- **Risk:** 0 minimal to 5 prohibited or likely harmful.

Calculate a 0–100 priority score:

```text
priority =
  relevance * 5 +
  site_quality * 4 +
  audience_value * 2 +
  viability * 4 +
  target_fit * 2 +
  cost_efficiency * 1 +
  (5 - difficulty) * 2
```

Apply risk as a gate, not a tradeable deduction:

- risk 0–2: may proceed according to controls;
- risk 3: require human review before external action;
- risk 4–5 or prohibited tactic: reject regardless of score.

Suggested priority bands:

- 75–100: priority;
- 60–74: pursue;
- 45–59: monitor or improve the target asset first;
- below 45: archive unless new evidence materially changes the record.

Do not inflate scores when data is missing. Mark unknown fields and reduce confidence.

## Status model

Use these normalized statuses:

```text
discovered
qualified
needs-review
queued
registration-started
verification-pending
outreach-proposed
approval-pending
outreach-sent
response-received
placement-promised
verified
declined
rejected
dormant
lost
```

`placement-promised` is never success. `verified` requires live-web evidence. A previously verified link becomes `lost` when it disappears, stops resolving to the target, becomes inaccessible for repeated checks, or materially changes; preserve prior verification history.

## Attempt history

Append an attempt for every material action or observation:

- research/qualification decision;
- registration or form submission;
- verification message received/opened;
- outreach proposal, approval, send, bounce, reply, decline, or suppression;
- promised placement;
- verification check and result;
- changed or removed backlink.

Record timestamp, actor, action type, channel, outcome, evidence, and next step. Never erase a failed attempt to make conversion metrics look better.

## Verification standard

Record a backlink as web-verified only after inspecting the current source page and confirming:

1. the source page resolves and is not an error/placeholder;
2. the link is present in page content and navigable, not merely plain text or hidden markup;
3. its final destination belongs to the authorized target domain or exact approved target URL;
4. redirect behavior is understood and not deceptive;
5. anchor text and surrounding context are accurate;
6. `rel` attributes and placement type are recorded;
7. the link is not hidden, injected, or obviously compromised.

Record `nofollow`, `ugc`, and `sponsored` links accurately. They may have audience or referral value but must not be reported as unqualified editorial links.
