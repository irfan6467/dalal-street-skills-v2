# Full-skill blind replay: pre-registration

Irfan asked (3 Nov 2026) for the whole Dalal Street skill chain to be run on a past NSE session that Claude had not
seen. `rules.json` was written first, without a test day; the test day was then drawn from the SHA-256 of that file
(`draw.json`): index 5 of 10, **Thursday 15 Oct 2026**, signal day **Wednesday 14 Oct 2026**. This file, `rules.json`
and `draw.json` are committed and pushed before any data dated 14 or 15 Oct is read for this test.

Honest limits, stated before the result exists:

- The draw is checkable (anyone can hash `rules.json`), but the author of `rules.json` could in principle have
  rewritten it until a liked day came up. That was not done, and nothing about 8-22 Oct was known to choose by.
- Past data is on disk: the NSE bhavcopy cache from the 2 Nov test covers 1 Sep 2025 to 30 Oct 2026, and
  `tests/fixtures/nse-scanner/` holds real bhavcopies for 13-23 Oct. The wall is procedural: the screen reads a
  folder holding only files named up to 14 Oct; the fixtures are not opened; test-day bars are fetched after the
  picks are locked, and each skill call gets them cut at its own clock.
- The late-October facts Claude has seen are listed in `rules.json`. Every decision below is mechanical, so they
  cannot steer it, but they are disclosed.
- Big Money and Shoonya do not run (no point-in-time bulk/block file; Shoonya needs Irfan's laptop). Analyst output
  is shown only for periods ending by 30 Jun 2026. News is cut at 09:00 IST on the test day by publication time.
- The broker's "yes" is typed by Claude under the rule in `rules.json`: this is a replay, not Irfan's consent, and
  nothing is sent to any broker.
- One day is one sample.

Steps: (1) screen with backtest, scanner, news and analyst on data to 14 Oct, lock `picks.json`, push;
(2) fetch 15 Oct bars and run alerts, entry, exit, time machine, budget, risk and broker on them; push the result.
