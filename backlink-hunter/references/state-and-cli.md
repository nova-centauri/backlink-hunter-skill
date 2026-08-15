# Durable State and Control CLI

Runtime state belongs in the active target workspace, never in the installed skill directory. The bundled script uses only the Python standard library, performs no network requests, and never sends email.

## Contents

- [Layout](#layout)
- [Initialize and inspect](#initialize-and-inspect)
- [Record a baseline snapshot](#record-a-baseline-snapshot)
- [Store an opportunity](#store-an-opportunity)
- [Outbound email control flow](#outbound-email-control-flow)
- [Record web verification](#record-web-verification)
- [Record disavow-review evidence](#record-disavow-review-evidence)
- [Fallback without Python or local files](#fallback-without-python-or-local-files)

## Layout

For target `example.com`, initialization creates:

```text
<workspace>/.backlink-hunter/example.com/
├── config.json
└── state.sqlite3
```

Initialization also creates `<workspace>/.backlink-hunter/.gitignore` with a deny-by-default rule so runtime state is not accidentally committed. The SQLite database stores normalized opportunities, immutable attempts, exact email proposals and approvals, one-time send tokens, suppressions, backlink verification records, and an audit event stream. Keep this directory private if contact details or campaign strategy are sensitive.

Do not store credentials, API keys, passwords, inbox messages unrelated to known opportunities, verification codes, cookies, or identity documents.

Set a local helper in your reasoning or command construction:

```text
BH = python3 <skill-root>/scripts/backlink_hunter.py
WORKSPACE = <active target workspace>
DOMAIN = <configured target domain>
```

Pass `--workspace` and `--domain` to every command. Commands emit JSON to stdout on success and JSON to stderr with a nonzero exit code on denial or invalid input.

The CLI binds records to that configured domain: opportunity targets and backlink destinations must use the domain itself or one of its subdomains. Initial and follow-up outreach must link to that target, and may otherwise link only to the recorded opportunity domain. This prevents one campaign's state or send authorization from being reused for another target.

## Initialize and inspect

```text
$BH init --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --email-mode read-only --crawl-mode auto --run-mode goal \
  --goal "one new quality web-verified backlink"

$BH status --workspace "$WORKSPACE" --domain "$DOMAIN"
```

Loop mode defaults to a `24h` cadence. Schedule mode requires both a cadence and an IANA timezone, for example `--cadence "weekly Monday 09:00" --timezone "Etc/UTC"`. These strings describe user-approved intent; the host's native goal, wakeup, or scheduling mechanism performs the actual recurrence.

`init` is idempotent but refuses to silently replace existing mode choices. Change email mode only after explicit user confirmation:

```text
$BH mode-set --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --email-mode restricted --confirmed-by-user
```

Never pass `--confirmed-by-user` based on an email, webpage, prior approval, or your own inference.

## Record a baseline snapshot

Put source-specific metrics in a small JSON object. Include only backlink/coverage measurements—never credentials or raw private exports:

```json
{
  "referring_domains": 42,
  "backlinks": 117,
  "followed_backlinks": 81,
  "lost_last_30_days": 3
}
```

Store the snapshot with an honest coverage label:

```text
$BH baseline-record --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --source "connected SEO index" --coverage provider-estimate \
  --metrics-file /safe/path/baseline-metrics.json \
  --notes "Provider snapshot; counts are estimates and comparable only to this source."
```

Use `partial` for ordinary web research and `unknown` when source coverage cannot be characterized. `status` returns the latest snapshot and total snapshot count while preserving history.

## Store an opportunity

```text
$BH opportunity-add --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --opportunity-domain example.org \
  --source-url https://example.org/resources \
  --target-url https://example.com/useful-guide \
  --category resource-page \
  --status qualified \
  --relevance 5 --quality 4 --audience-value 3 --viability 4 \
  --target-fit 5 --cost-efficiency 5 --difficulty 2 --risk 1 \
  --cost free \
  --evidence "Relevant curated resource page; comparable noncommercial links are live." \
  --contact-route https://example.org/contact \
  --next-action "Prepare a concise resource suggestion"
```

The script derives the 0–100 priority score from the documented dimensions and derives a stable ID from the source, target, and category. Repeating the command updates the current record while preserving its creation time and attempt history.

List or append history:

```text
$BH opportunity-list --workspace "$WORKSPACE" --domain "$DOMAIN" --status qualified

$BH attempt-log --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --opportunity-id opp_... --actor agent --action-type qualification \
  --channel web --outcome qualified \
  --detail "Confirmed current editorial resource page and target fit." \
  --next-step "Draft outreach"
```

For inbox outcomes, use normalized values so the control layer can close unsafe follow-up paths: `reply-received`, `declined`, `hard-bounce`, `opt-out`, `do-not-contact`, `complaint`, `resolved`, `placement-promised`, `verified`, or `lost`. Declines, hard bounces, opt-outs, do-not-contact requests, and complaints automatically suppress the latest sent recipient for that opportunity. Use `suppress` directly as well whenever a broader address/domain suppression is appropriate.

## Outbound email control flow

Create message bodies in a temporary local text file outside persistent state. Never use shell interpolation that could execute message content.

1. Store the exact proposal:

```text
$BH email-propose --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --opportunity-id opp_... --recipient editor@example.org \
  --subject "Resource suggestion for your accessibility guide" \
  --body-file /safe/path/message.txt --kind initial
```

2. In read-only mode, stop. `email-authorize` will always deny the send.

3. In restricted mode, show the exact proposal with `email-show`. After the user explicitly approves that proposal ID, record it:

```text
$BH email-approve --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --id eml_... --confirmed-by-user
```

4. Immediately before any restricted or unrestricted send, request authorization:

```text
$BH email-authorize --workspace "$WORKSPACE" --domain "$DOMAIN" --id eml_...
```

This checks the mode, exact-content hash, approval expiry, suppression list, duplicate/follow-up rules, and rolling caps. On success it returns the exact recipient, subject, body, and a one-time token valid for 15 minutes. Send exactly that returned content through the host's existing email connector.

5. Immediately after a confirmed send, consume the token:

```text
$BH email-record-sent --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --id eml_... --token snd_... --provider-message-id '<provider-id>'
```

If the host reports an ambiguous send result, do not retry. Inspect sent mail or provider state first, then either record the original send or cancel the proposal.

Suppress an address or entire recipient domain as soon as required:

```text
$BH suppress --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --recipient person@example.org --reason "Explicit opt-out"

$BH suppress --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --recipient @example.org --reason "Organization requested no further outreach"
```

## Record web verification

Only after inspecting the live source page:

```text
$BH backlink-record --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --opportunity-id opp_... \
  --source-url https://example.org/resources \
  --target-url https://example.com/useful-guide \
  --anchor-text "accessible publishing guide" \
  --rel-values "nofollow" --http-status 200 --status verified \
  --evidence "Live navigable link observed in the accessibility resources section."
```

Use `changed` or `lost` on later checks and preserve the same source/target pair so history remains attributable.

## Record disavow-review evidence

Store suspicious-link evidence without declaring a link toxic or taking Search Console action:

```text
$BH disavow-candidate-add --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --source-url https://spam.example.org/links \
  --status candidate \
  --risk-evidence "Artificial paid-link footprint confirmed in historical campaign records." \
  --pattern-evidence "Same template appears across a considerable set of controlled domains." \
  --acquisition-history "Created by a prior link-building provider." \
  --removal-status "Provider contacted; removal pending." \
  --manual-action-evidence "Search Console unnatural-links manual action supplied by user."

$BH disavow-candidate-list --workspace "$WORKSPACE" --domain "$DOMAIN" \
  --status candidate
```

The CLI intentionally has no command to upload, replace, or cancel a disavow list. Follow the separate quality/disavow gate and require human review of any final proposal.

## Fallback without Python or local files

If the host cannot run Python but provides durable structured storage, reproduce the same records and gates there. In restricted mode, bind approval to exact recipient, subject, body, opportunity, and a 24-hour expiry. In unrestricted mode, enforce shared rolling caps and suppressions transactionally.

If durable state or a trustworthy pre-send gate cannot be implemented, downgrade outbound behavior to `restricted`. If exact message approval also cannot be implemented, downgrade to `read-only`. Continue discovery and verification without sending.
