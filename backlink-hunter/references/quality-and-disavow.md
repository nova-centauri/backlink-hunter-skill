# Quality, Link-Spam, and Disavow Policy

Apply this policy when qualifying opportunities and reviewing existing suspicious backlinks.

## Quality principles

A valuable backlink should have a plausible reason to exist for a human reader. Evaluate topical/local relevance, source identity, editorial standards, real audience, placement context, target-page usefulness, and acquisition method together. Do not treat a vendor authority score as proof of quality or a low score as proof of toxicity.

Reject opportunities involving:

- payment, goods, services, or gifts in exchange for links that pass ranking credit;
- excessive reciprocal linking or partner pages built only for cross-linking;
- automated link creation, low-quality directory blasts, comment/forum spam, or distributed widget/template links;
- optimized-anchor advertorials, mass guest posts, or press releases intended to manipulate rankings;
- private blog networks, expired-domain networks, hacked/injected/hidden links, cloaking, or fake profiles;
- content or pages created primarily to exploit another site's reputation or ranking signals.

Legitimate advertising and sponsorship can have referral or brand value, but require separate user approval and correct `rel="sponsored"` or `nofollow` qualification. Record them as sponsored placements, not earned editorial backlinks.

Primary guidance:

- [Google Search spam policies](https://developers.google.com/search/docs/essentials/spam-policies)
- [Google guidance for qualifying outbound links](https://developers.google.com/search/docs/crawling-indexing/qualify-outbound-links)

## Suspicious-link review

Use the label `disavow-candidate`, not `toxic`, until evidence supports a specific concern. Record:

- source URL/domain and discovery source;
- acquisition history, if known;
- concrete spam/artificial-link evidence;
- scale/pattern across referring domains;
- whether the user or a hired provider created or paid for the links;
- removal attempt and response, when applicable;
- manual-action evidence or a reasoned likelihood of one;
- risk of accidentally excluding legitimate links.

Common weak signals—low authority metric, unfamiliar TLD, foreign language, missing traffic estimate, or a single odd scraper link—are not sufficient on their own.

## Disavow gate

Google describes disavowal as an advanced action that most sites do not need and warns that incorrect use can harm search performance. Recommend preparing a disavow proposal only when both are supported:

1. there is a considerable pattern of spammy, artificial, or low-quality links; and
2. the links caused a manual action or create a credible likelihood of one, commonly because the site or its SEO provider built or paid for them.

First make a good-faith effort to remove links the user or their provider created when practical. Do not contact random scraper sites merely because they copied a page.

Source: [Google Search Console: Disavow links to your site](https://support.google.com/webmasters/answer/2648487)

## Output and approval

Maintain evidence-rich candidates in durable state. A proposed export may use Google's documented text format, with comments and either a full URL or `domain:` entry per line. Preserve any existing disavow list because uploading a new list can replace it.

Never upload, replace, or cancel a disavow list automatically in any email or run mode. Require the user or qualified SEO reviewer to inspect the exact merged proposal and explicitly approve the Search Console action. Report uncertainty and the potential downside prominently.
