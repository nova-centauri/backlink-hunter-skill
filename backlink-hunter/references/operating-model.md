# Operating Model

Backlink Hunter separates broad discovery from conservative execution. Every activation runs a bounded cycle and leaves durable state for the next activation.

## Contents

- [Run modes](#run-modes)
- [Cycle budgets](#cycle-budgets)
- [Baseline requirements](#baseline-requirements)
- [Discovery lanes](#discovery-lanes)
- [Tool routing](#tool-routing)
- [Reporting and metrics](#reporting-and-metrics)

## Run modes

### Goal

Use a durable host goal when available. The default goal is **one new, quality, web-verified backlink** to the authorized target domain. Let the user set a different measurable goal, quality floor, target page, or deadline.

Complete a goal only when its success condition is web-verified. A sent pitch, created account, submitted listing, or promised placement is progress, not completion. If the host has no goal primitive, persist the same success condition and resume it in later sessions.

### Loop

Run one cycle per wakeup and persist before the next. Default to a 24-hour interval; use a slower interval when sites, inboxes, or provider limits warrant it. Never implement a loop as a tight shell loop, long blocking sleep, repeated refresh, or concurrent agent swarm.

Stop or pause the loop when:

- the user asks;
- authorization is withdrawn;
- the target or inbox becomes unavailable;
- outbound controls cannot be enforced;
- a safety incident occurs;
- the configured goal is complete and no continuing loop was explicitly requested;
- further progress requires money, a contract, sensitive data, or user judgment.

If the host cannot schedule wakeups, run one cycle and provide the exact prompt needed to resume from state.

### Schedule

Use the host's native recurring-task or automation mechanism. Before creating it, obtain an exact cadence, local time, and timezone. Each occurrence runs one bounded cycle, not an unbounded subprocess.

Recommended starting cadences:

- discovery and opportunity refresh: weekly;
- inbox/reply and expected-verification checks: daily when active;
- promised-placement verification: every 3–7 days;
- verified-link health check: monthly;
- full baseline refresh: monthly or after a material campaign change.

Do not multiply schedules to evade crawl or email limits. Scheduled runs inherit the configured email mode and cannot upgrade it.

## Cycle budgets

Unless the user configures lower bounds, use these ceilings per cycle:

- inspect at most 100 newly discovered raw candidates;
- deeply qualify at most 25 candidates;
- advance at most 10 opportunities through non-email actions;
- inspect only relevant recent inbox threads;
- obey the email caps regardless of cycle size;
- verify all due promised placements and a reasonable sample of previously verified links;
- perform no more than one baseline snapshot per day.

These limits prevent a single cycle from becoming a crawler or submission flood. Split a large import across later cycles.

## Baseline requirements

Capture:

- target domain and canonical site URL;
- timestamp and each data source's coverage date;
- whether counts are exact, provider estimates, partial, or unknown;
- referring-domain and backlink totals when a suitable data source exists;
- followed/nofollow/sponsored/UGC distribution when available;
- important target pages and known linkable assets;
- highest-value existing referring domains and recently gained/lost links;
- suspicious-link candidates without automatically labeling them toxic;
- a reasoned competitor set;
- any user-supplied Search Console manual-action status;
- known disavow file/status, if the user supplies it.

Save snapshots with timestamps. Compare like-for-like sources when reporting changes; provider totals are not directly interchangeable.

## Discovery lanes

Rotate across lanes so the pipeline is not dominated by one tactic:

1. competitor link gaps;
2. unlinked mentions and citation corrections;
3. broken/outdated resources;
4. curated resources and genuine editorial fit;
5. local, professional, association, chamber, and membership listings;
6. partner/supplier/customer/integration relationships;
7. original data, tools, guides, and other target-owned linkable assets;
8. credible expert-source or journalist opportunities;
9. historical lead discovery through archives.

Prefer opportunities that already have a clear human benefit and legitimate reason to link. If the target lacks a suitable destination page, recommend improving or creating a useful asset instead of pitching a weak page.

## Tool routing

- Use SEO/backlink tools for indexed backlink sets, authority context, gained/lost links, and competitor gaps.
- Use crawlers for source-page extraction, broken-link checks, canonical/redirect inspection, and verification.
- Use ordinary web search/browser access for discovery, manual qualification, and fallbacks.
- Use the user's Search Console or exports only when connected or supplied; do not imply access.
- Use the host inbox connector for inbox work; this skill does not provide credentials or an email service.
- Use the host approval system for restricted-mode approval when it can bind approval to exact content; otherwise use the bundled local proposal ID plus explicit in-chat approval.

Record which tool produced each material fact. Never merge estimates into a false precise number.

## Reporting and metrics

Track leading and outcome metrics separately:

- raw candidates discovered;
- qualified and rejected opportunities;
- registrations/submissions completed;
- outreach proposed, approved, sent, bounced, declined, and answered;
- placements promised;
- new web-verified backlinks;
- verified links retained, changed, or lost;
- referring-domain change using comparable snapshots;
- median time from discovery to verification;
- conversion from qualified opportunity to verified link.

Do not optimize send volume. The primary outcome is durable, relevant, verified links; the primary constraint is responsible stewardship of the represented domain.
