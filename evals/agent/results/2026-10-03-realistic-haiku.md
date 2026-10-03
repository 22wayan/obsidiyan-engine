# Agent eval 2026-10-03, realistic history, Claude Haiku, three runs

18 questions per run, 54 answers per condition. History: the 18 Ledgerly sessions plus 240 sessions from three other fictional projects, with tool calls, tool output and thinking blocks; user text is 6.2% of the log bytes (7.3% in the author's real Claude Code logs).

| Condition | Correct (3 runs) | Avg input tokens | Avg cost (USD) | Avg seconds |
|---|---|---|---|---|
| none | 0/54 | 3,693 | 0.0059 | 4.0 |
| raw-logs | 53/54 | 30,256 | 0.0162 | 9.3 |
| obsidiyan | 50/54 | 20,565 | 0.0103 | 6.8 |

Misses: obsidiyan answered q03 (current payment provider) with Stripe in all three runs. The query "payment provider" matches the older Ledgerly decision and a distractor project that still uses Stripe, while the session that switched to Mollie never says "provider". raw-logs and obsidiyan each missed q11 once (only magic links, passkeys not mentioned).

Per-run data: `2026-10-03-realistic-haiku-run1.json` to `run3.json`.
