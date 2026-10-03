#!/usr/bin/env python3
"""Erzeugt die fiktive Session-Historie fuer den Agent-Eval.

Ledgerly ist ein erfundenes Rechnungs-SaaS. Die Sessions enthalten
Entscheidungen, Begruendungen, Bugs und zwei Kurswechsel, verteilt ueber
vier Monate. Die Assistant-Turns tragen Code und Erklaertext, wie echte
Logs auch, damit ein Agent, der die Rohdateien liest, realistisch viel
Rauschen sieht.

Usage: python evals/agent/build_history.py [--realistic]

Ohne Flag entstehen 18 saubere Sessions in evals/agent/history/. Mit
--realistic entsteht evals/agent/history-realistic/: dieselben 18 Sessions,
jede mit Werkzeug-Aufrufen, Werkzeug-Ausgaben und Denkschritten, wie sie
echte Claude-Code-Logs fuellen, dazu 240 Sessions aus drei anderen fiktiven
Projekten mit ueberlappendem Vokabular. In echten Logs ist der Nutzertext nur
ein kleiner Teil der Datei; diese Variante bildet das nach.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "history" / "-home-dev-ledgerly"
CWD = "/home/dev/ledgerly"

CODE = {
    "py": "```python\nfrom decimal import Decimal\n\n\ndef total_cents(items: list[dict]) -> int:\n    return sum(i['qty'] * i['unit_cents'] for i in items)\n\n\nclass InvoiceSerializer(serializers.ModelSerializer):\n    class Meta:\n        model = Invoice\n        fields = ['id', 'number', 'issued_on', 'total_cents', 'status']\n```",
    "sql": "```sql\nCREATE TABLE invoices (\n  id bigserial PRIMARY KEY,\n  number text UNIQUE NOT NULL,\n  customer_id bigint REFERENCES customers(id),\n  issued_on date NOT NULL,\n  total_cents bigint NOT NULL,\n  status text NOT NULL DEFAULT 'draft'\n);\nCREATE INDEX invoices_customer_idx ON invoices(customer_id);\n```",
    "yaml": "```yaml\napp = 'ledgerly'\nprimary_region = 'fra'\n[http_service]\n  internal_port = 8000\n  force_https = true\n  min_machines_running = 2\n[[vm]]\n  memory = '512mb'\n```",
    "ts": "```ts\nexport async function fetchInvoices(page = 1) {\n  const res = await fetch(`/api/invoices?page=${page}`, { credentials: 'include' });\n  if (!res.ok) throw new Error(`API ${res.status}`);\n  return (await res.json()) as InvoicePage;\n}\n```",
}

FILLER = (
    "I checked the related modules and the test suite before changing anything. "
    "The change touches the serializer, the migration and two views; the rest of "
    "the code base stays as it is. I ran the tests locally and they pass. "
    "Let me know if you want me to split this into smaller pull requests."
)

# (session_id, date, title, [(user_text, code_key), ...])
SESSIONS: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    ("s01-stack", "2026-03-02", "Pick the backend stack", [
        ("We start Ledgerly as one Django monolith with Django REST Framework. No microservices: we are two developers and one deploy target is enough. Frontend is a small React app that talks to the DRF API.", "py"),
        ("Store all money as integer cents in bigint columns, never as floats or Decimal in the database. Rounding happens once, when we render the invoice.", "sql"),
    ]),
    ("s02-payments", "2026-03-09", "Choose the payment provider", [
        ("I compared Stripe and Mollie for payments. We take Stripe: it supports SEPA Direct Debit and its webhooks are better documented. Please scaffold the Stripe client and the webhook endpoint.", "py"),
    ]),
    ("s03-numbers", "2026-03-16", "Invoice number format", [
        ("Invoice numbers follow the format LDG-YYYY-NNNNN, for example LDG-2026-00042. The counter resets every January. Numbers must be gapless, German tax rules (GoBD) do not allow holes, so we allocate them inside the same transaction that finalises the invoice.", "sql"),
    ]),
    ("s04-pdf", "2026-03-24", "Generate invoice PDFs", [
        ("Puppeteer keeps crashing: headless Chrome needs more than the 512 MB our container has. We switch PDF rendering to WeasyPrint, which renders our HTML templates without a browser.", "py"),
    ]),
    ("s05-tests", "2026-04-01", "Test database setup", [
        ("Tests run against a real Postgres started with testcontainers. No SQLite in tests anymore, it hid two bugs with bigint and with row locking.", "py"),
    ]),
    ("s06-timezone", "2026-04-14", "Invoices dated one day off", [
        ("Customers report invoices dated one day too early when they finalise them shortly after midnight. Root cause: the server runs in UTC and we took the date from a UTC timestamp. Fix: derive issued_on from the Europe/Berlin local date, and add a test for 00:30 local time.", "py"),
    ]),
    ("s07-email", "2026-04-22", "Transactional email", [
        ("For sending invoices by email we take Postmark, not SendGrid. In our test, SendGrid mails landed in spam at GMX and web.de, which most of our German customers use.", "py"),
    ]),
    ("s08-auth", "2026-05-05", "Login without passwords", [
        ("Users log in with magic links sent by email. We do not store passwords at all. Links expire after 15 minutes and work once.", "py"),
    ]),
    ("s09-flags", "2026-05-12", "Feature flags", [
        ("We introduce feature flags with a self-hosted Unleash instance, so we can roll out dunning to a few customers first.", "yaml"),
    ]),
    ("s10-duplicates", "2026-05-20", "Duplicate invoices after webhook retries", [
        ("Some customers got two paid invoices. Stripe retried the webhook after our endpoint timed out, and we created a second payment record. Fix: an idempotency key on the payment intent id, with a unique constraint, so a retried webhook is a no-op.", "sql"),
    ]),
    ("s11-pricing", "2026-05-28", "Pricing tiers", [
        ("Pricing: Starter costs 9 euros per month, Pro costs 29 euros per month. Pro adds automatic dunning and custom invoice templates.", "ts"),
    ]),
    ("s12-dunning", "2026-06-03", "Dunning schedule", [
        ("Dunning sends reminders 7, 14 and 30 days after the due date. The third reminder adds a late fee of 5 euros, the first two are friendly reminders without a fee.", "py"),
    ]),
    ("s13-flags-removed", "2026-06-10", "Remove Unleash", [
        ("Unleash is overkill for us and the server needs updates every month. We remove it and replace every flag with an environment variable read at startup. Please delete the Unleash client and the docker service.", "yaml"),
    ]),
    ("s14-mollie", "2026-06-17", "Switch payments to Mollie", [
        ("We move all payments from Stripe to Mollie. Stripe charges a percentage on SEPA Direct Debit, Mollie charges a flat fee per transaction, which is much cheaper for invoices above 100 euros. New customers go to Mollie from today, existing Stripe mandates are migrated by the end of July.", "py"),
    ]),
    ("s15-ratelimit", "2026-06-24", "Public API rate limit", [
        ("The public API allows 60 requests per minute per API key. Above that we answer 429 with a Retry-After header.", "py"),
    ]),
    ("s16-backups", "2026-07-01", "Database backups", [
        ("Nightly pg_dump to S3-compatible object storage at Hetzner, kept for 30 days. We test a restore on the first Monday of every month.", "yaml"),
    ]),
    ("s17-i18n", "2026-07-08", "Translations", [
        ("The UI is German first. English comes later, so we already wrap every string with i18next now instead of hardcoding German text.", "ts"),
    ]),
    ("s18-passkeys", "2026-07-15", "Add passkeys", [
        ("Magic links stay, but we add passkeys as a second way to log in. Users who registered a passkey skip the email round trip.", "ts"),
        ("Deploy target stays Fly.io in the Frankfurt region with two machines; nothing changes there with the passkey release.", "yaml"),
    ]),
]


def _assistant(code_key: str) -> str:
    return f"Understood, here is the change.\n\n{CODE[code_key]}\n\n{FILLER}"


def _machine_traffic(rng: random.Random, sid: str, n: int, project: str) -> list[dict]:
    """Werkzeug-Aufruf, Werkzeug-Ausgabe und Denkschritt, wie in echten Logs."""
    files = ["src/models.py", "src/views.py", "src/billing/service.py", "tests/test_api.py",
             "migrations/0007_auto.py", "package.json", "fly.toml", "README.md"]
    path = rng.choice(files)
    body = "\n".join(
        f"{i:4d}  " + rng.choice(list(CODE.values())).replace("\n", " ")[: rng.randint(60, 140)]
        for i in range(1, rng.randint(5, 12))
    )
    tool_id = f"toolu_{sid}_{n}"
    thinking = " ".join(
        rng.choice([
            "The user wants this change in the existing module.",
            "I should check the tests before editing.",
            f"In {project} the billing code lives in a service layer.",
            "Rate limits, payments and invoices are touched by several views.",
            "I will read the file first, then propose a minimal diff.",
        ])
        for _ in range(rng.randint(4, 9))
    )
    return [
        {"type": "assistant", "sessionId": sid, "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": thinking},
            {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": path}},
        ]}},
        {"type": "user", "sessionId": sid, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": body},
        ]}},
    ]


DISTRACTOR_PROJECTS = ["shopfront", "metrics", "mobile"]
DISTRACTOR_LINES = [
    "In {p} we keep using Stripe as payment provider for card payments, nothing changes there.",
    "The {p} API rate limit stays at 120 requests per minute for internal clients.",
    "Please add pagination to the invoice export in {p}; the CSV gets too big.",
    "For {p} translations we use a JSON file per language, no library for now.",
    "{p} backups run weekly, we keep them for 14 days on the NAS.",
    "The {p} tests are slow; run them in parallel with pytest-xdist.",
    "Fix the flaky login test in {p}, it fails when the CI runner is under load.",
    "Rename the settings module in {p} and update the imports.",
    "In {p} we store prices as Decimal, the shop needs fractional cents for currency conversion.",
    "Upgrade Django in {p} to the next LTS and check the deprecation warnings.",
    "The {p} dashboard needs a chart for monthly revenue per customer segment.",
    "Move the {p} cron jobs to a separate worker process.",
]


def build_realistic(n_distractors: int = 240, seed: int = 7) -> int:
    rng = random.Random(seed)
    root = HERE / "history-realistic"
    count = 0
    for sid, day, title, turns in SESSIONS:
        rows: list[dict] = [{"type": "ai-title", "aiTitle": title, "sessionId": sid}]
        for n, (text, code_key) in enumerate(turns):
            ts = f"{day}T09:{10 + 5 * n:02d}:00+00:00"
            rows.append({"type": "user", "sessionId": sid, "timestamp": ts, "cwd": CWD,
                         "gitBranch": "main", "message": {"role": "user", "content": text}})
            for k in range(rng.randint(1, 3)):
                rows += _machine_traffic(rng, sid, n * 10 + k, "ledgerly")
            rows.append({"type": "assistant", "sessionId": sid, "timestamp": ts,
                         "message": {"role": "assistant",
                                     "content": [{"type": "text", "text": _assistant(code_key)}]}})
        out = root / "-home-dev-ledgerly"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
        count += 1
    for i in range(n_distractors):
        project = DISTRACTOR_PROJECTS[i % len(DISTRACTOR_PROJECTS)]
        sid = f"d{i:03d}-{project}"
        day = f"2026-{rng.randint(3, 7):02d}-{rng.randint(1, 28):02d}"
        rows = [{"type": "ai-title", "aiTitle": f"{project} maintenance {i}", "sessionId": sid}]
        for n in range(rng.randint(2, 4)):
            ts = f"{day}T1{n}:00:00+00:00"
            text = rng.choice(DISTRACTOR_LINES).format(p=project)
            rows.append({"type": "user", "sessionId": sid, "timestamp": ts,
                         "cwd": f"/home/dev/{project}", "gitBranch": "main",
                         "message": {"role": "user", "content": text}})
            for k in range(rng.randint(1, 3)):
                rows += _machine_traffic(rng, sid, n * 10 + k, project)
            rows.append({"type": "assistant", "sessionId": sid, "timestamp": ts,
                         "message": {"role": "assistant", "content": [
                             {"type": "text", "text": _assistant(rng.choice(list(CODE)))}]}})
        out = root / f"-home-dev-{project}"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
        count += 1
    return count


def build() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for sid, day, title, turns in SESSIONS:
        rows: list[dict] = [{"type": "ai-title", "aiTitle": title, "sessionId": sid}]
        for n, (text, code_key) in enumerate(turns):
            ts = f"{day}T09:{10 + 5 * n:02d}:00+00:00"
            rows.append({"type": "user", "sessionId": sid, "timestamp": ts, "cwd": CWD,
                         "gitBranch": "main", "message": {"role": "user", "content": text}})
            rows.append({"type": "assistant", "sessionId": sid, "timestamp": ts,
                         "message": {"role": "assistant",
                                     "content": [{"type": "text", "text": _assistant(code_key)}]}})
        path = OUT / f"{sid}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    if "--realistic" in sys.argv[1:]:
        print(f"{build_realistic()} sessions in {HERE / 'history-realistic'}")
    else:
        for p in build():
            print(p.name)
