#!/usr/bin/env python3
"""Portable local state and outbound-email guardrails for Backlink Hunter.

This script never accesses the network or sends email. It stores campaign state,
binds restricted-mode approvals to exact message content, enforces suppressions
and volume limits, and issues short-lived one-time send authorizations for the
host agent's existing email connector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA_VERSION = 1
EMAIL_LIMIT_24H = 5
EMAIL_LIMIT_7D = 20
FOLLOWUP_WAIT_DAYS = 7
APPROVAL_HOURS = 24
TOKEN_MINUTES = 15
MAX_BODY_WORDS = 120
MAX_SUBJECT_CHARS = 70

EMAIL_MODES = ("read-only", "restricted", "unrestricted")
CRAWL_MODES = ("auto", "standard-web", "off")
RUN_MODES = ("goal", "loop", "schedule")
EMAIL_KINDS = ("initial", "followup", "reply")
FOLLOWUP_BLOCKING_OUTCOMES = (
    "reply-received",
    "declined",
    "hard-bounce",
    "opt-out",
    "do-not-contact",
    "complaint",
    "resolved",
    "placement-promised",
    "verified",
)
OUTCOME_STATUS = {
    "reply-received": "response-received",
    "declined": "declined",
    "hard-bounce": "declined",
    "opt-out": "declined",
    "do-not-contact": "declined",
    "complaint": "declined",
    "placement-promised": "placement-promised",
    "verified": "verified",
    "lost": "lost",
}
AUTO_SUPPRESSION_OUTCOMES = {
    "declined": "Explicit decline",
    "hard-bounce": "Hard bounce",
    "opt-out": "Opt-out",
    "do-not-contact": "Do-not-contact request",
    "complaint": "Outreach complaint",
}
OPPORTUNITY_STATUSES = (
    "discovered",
    "qualified",
    "needs-review",
    "queued",
    "registration-started",
    "verification-pending",
    "outreach-proposed",
    "approval-pending",
    "outreach-sent",
    "response-received",
    "placement-promised",
    "verified",
    "declined",
    "rejected",
    "dormant",
    "lost",
)
DISAVOW_STATUSES = ("candidate", "needs-review", "excluded", "proposed")

EMAIL_RE = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.I)
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
WORD_RE = re.compile(r"\b[\w’'-]+\b", re.UNICODE)
HTML_RE = re.compile(r"<(?:a|img|html|body|script|style)\b", re.I)


class ControlError(RuntimeError):
    """Expected safety or validation failure."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: Optional[datetime] = None) -> str:
    value = value or now_utc()
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def emit(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def fail(message: str, *, code: int = 2, **details: Any) -> None:
    payload: Dict[str, Any] = {"ok": False, "error": message}
    payload.update(details)
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def normalize_domain(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ControlError("Target domain is required")
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if parsed.username or parsed.password:
        raise ControlError("Target domain must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ControlError("Target domain contains an invalid port") from exc
    if port is not None:
        raise ControlError("Target domain must not contain a port")
    hostname = parsed.hostname
    if not hostname:
        raise ControlError("Could not parse a hostname from target domain")
    try:
        hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ControlError("Target domain contains an invalid hostname") from exc
    if hostname == "localhost" or "." not in hostname:
        raise ControlError("Target domain must be a public-looking domain name")
    labels = hostname.split(".")
    if any(not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in labels):
        raise ControlError("Target domain contains an invalid hostname label")
    return hostname


def normalize_email(raw: str) -> str:
    value = raw.strip().lower()
    if not EMAIL_RE.fullmatch(value):
        raise ControlError("Recipient is not a valid email address")
    return value


def normalize_suppression(raw: str) -> str:
    value = raw.strip().lower()
    if value.startswith("@"):
        domain = normalize_domain(value[1:])
        return f"@{domain}"
    return normalize_email(value)


def normalize_http_url(raw: str, label: str) -> Tuple[str, str]:
    """Return a trimmed absolute HTTP(S) URL and its normalized hostname."""
    value = raw.strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ControlError(f"{label} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ControlError(f"{label} must not contain credentials")
    try:
        parsed.port
    except ValueError as exc:
        raise ControlError(f"{label} contains an invalid port") from exc
    return value, normalize_domain(parsed.hostname)


def domain_matches(hostname: str, authorized_domain: str) -> bool:
    """Accept the configured domain itself and any of its subdomains."""
    normalized_host = normalize_domain(hostname)
    normalized_authorized = normalize_domain(authorized_domain)
    return normalized_host == normalized_authorized or normalized_host.endswith(f".{normalized_authorized}")


def require_url_on_domain(raw: str, authorized_domain: str, label: str) -> str:
    value, hostname = normalize_http_url(raw, label)
    if not domain_matches(hostname, authorized_domain):
        raise ControlError(f"{label} must belong to authorized target domain {normalize_domain(authorized_domain)}")
    return value


def extract_http_urls(text: str) -> list[str]:
    # Remove punctuation commonly placed immediately after a prose URL.
    return [match.rstrip(".,;:!?)]}'\"") for match in URL_RE.findall(text)]


def state_dir(workspace: str, domain: str) -> Path:
    safe_domain = normalize_domain(domain).replace(":", "_")
    return Path(workspace).expanduser().resolve() / ".backlink-hunter" / safe_domain


def config_path(workspace: str, domain: str) -> Path:
    return state_dir(workspace, domain) / "config.json"


def database_path(workspace: str, domain: str) -> Path:
    return state_dir(workspace, domain) / "state.sqlite3"


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_config(workspace: str, domain: str) -> Dict[str, Any]:
    path = config_path(workspace, domain)
    if not path.exists():
        raise ControlError(f"State is not initialized: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlError(f"Could not read valid config: {path}") from exc
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ControlError("Unsupported config schema version")
    if config.get("target_domain") != normalize_domain(domain):
        raise ControlError("Config target domain does not match requested domain")
    if config.get("email_mode") not in EMAIL_MODES:
        raise ControlError("Config contains an invalid email mode")
    return config


def connect(workspace: str, domain: str) -> sqlite3.Connection:
    path = database_path(workspace, domain)
    if not path.exists():
        raise ControlError(f"State database is not initialized: {path}")
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        PRAGMA journal_mode = WAL;

        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            opportunity_domain TEXT NOT NULL,
            source_url TEXT,
            target_url TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL,
            priority_score INTEGER NOT NULL,
            relevance INTEGER NOT NULL,
            quality INTEGER NOT NULL,
            audience_value INTEGER NOT NULL,
            viability INTEGER NOT NULL,
            target_fit INTEGER NOT NULL,
            cost_efficiency INTEGER NOT NULL,
            difficulty INTEGER NOT NULL,
            risk INTEGER NOT NULL,
            cost TEXT NOT NULL,
            evidence TEXT NOT NULL,
            contact_route TEXT,
            next_action TEXT,
            next_check_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
            occurred_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            action_type TEXT NOT NULL,
            channel TEXT NOT NULL,
            outcome TEXT NOT NULL,
            detail TEXT NOT NULL,
            next_step TEXT
        );

        CREATE TABLE IF NOT EXISTS emails (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            body_sha256 TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            proposed_at TEXT NOT NULL,
            approved_at TEXT,
            approval_expires_at TEXT,
            sent_at TEXT,
            provider_message_id TEXT,
            cancelled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS send_tokens (
            token TEXT PRIMARY KEY,
            email_id TEXT NOT NULL REFERENCES emails(id),
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS suppressions (
            suppression_key TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS baselines (
            id TEXT PRIMARY KEY,
            captured_at TEXT NOT NULL,
            source TEXT NOT NULL,
            coverage TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            notes TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS disavow_candidates (
            id TEXT PRIMARY KEY,
            source_url TEXT,
            source_domain TEXT NOT NULL,
            status TEXT NOT NULL,
            risk_evidence TEXT NOT NULL,
            pattern_evidence TEXT NOT NULL,
            acquisition_history TEXT NOT NULL,
            removal_status TEXT NOT NULL,
            manual_action_evidence TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backlinks (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT REFERENCES opportunities(id),
            source_url TEXT NOT NULL,
            target_url TEXT NOT NULL,
            anchor_text TEXT,
            rel_values TEXT NOT NULL,
            http_status INTEGER NOT NULL,
            status TEXT NOT NULL,
            first_verified_at TEXT,
            last_checked_at TEXT NOT NULL,
            evidence TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_emails_sent_at ON emails(sent_at);
        CREATE INDEX IF NOT EXISTS idx_emails_recipient ON emails(recipient);
        CREATE INDEX IF NOT EXISTS idx_attempts_opportunity ON attempts(opportunity_id);
        CREATE INDEX IF NOT EXISTS idx_opportunities_status ON opportunities(status);
        CREATE INDEX IF NOT EXISTS idx_baselines_captured_at ON baselines(captured_at);
        """
    )
    connection.commit()


def log_event(connection: sqlite3.Connection, event_type: str, data: Dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO events (occurred_at, event_type, data_json) VALUES (?, ?, ?)",
        (isoformat(), event_type, json.dumps(data, sort_keys=True, ensure_ascii=False)),
    )


def context(args: argparse.Namespace) -> Tuple[Dict[str, Any], sqlite3.Connection]:
    config = load_config(args.workspace, args.domain)
    connection = connect(args.workspace, args.domain)
    initialize_schema(connection)
    return config, connection


def row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def require_opportunity(connection: sqlite3.Connection, opportunity_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if row is None:
        raise ControlError(f"Unknown opportunity: {opportunity_id}")
    return row


def message_hash(recipient: str, subject: str, body: str, opportunity_id: str, kind: str) -> str:
    exact = "\n".join((recipient, subject, body, opportunity_id, kind)).encode("utf-8")
    return hashlib.sha256(exact).hexdigest()


def generated_id(prefix: str, seed: Optional[str] = None) -> str:
    if seed is None:
        suffix = uuid.uuid4().hex[:16]
    else:
        suffix = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{suffix}"


def command_init(args: argparse.Namespace) -> None:
    domain = normalize_domain(args.domain)
    if args.run_mode == "goal":
        execution = {
            "mode": "goal",
            "goal": args.goal or "one new quality web-verified backlink",
            "cadence": None,
            "timezone": args.timezone,
        }
    elif args.run_mode == "loop":
        execution = {
            "mode": "loop",
            "goal": args.goal,
            "cadence": args.cadence or "24h",
            "timezone": args.timezone,
        }
    else:
        if not args.cadence or not args.timezone:
            raise ControlError("Schedule mode requires --cadence and --timezone")
        try:
            ZoneInfo(args.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ControlError("Schedule timezone must be a valid IANA timezone, such as Etc/UTC") from exc
        execution = {
            "mode": "schedule",
            "goal": args.goal,
            "cadence": args.cadence,
            "timezone": args.timezone,
        }
    directory = state_dir(args.workspace, domain)
    directory.mkdir(parents=True, exist_ok=True)
    privacy_ignore = directory.parent / ".gitignore"
    if not privacy_ignore.exists():
        privacy_ignore.write_text("*\n!.gitignore\n", encoding="utf-8")
    path = config_path(args.workspace, domain)
    created = False
    if path.exists():
        config = load_config(args.workspace, domain)
        requested = (args.email_mode, args.crawl_mode, args.run_mode, execution)
        existing = (config["email_mode"], config["crawl_mode"], config["run_mode"], config.get("execution"))
        if requested != existing:
            raise ControlError(
                "State already exists with different modes; use mode-set for email changes and edit other modes only after user confirmation"
            )
    else:
        timestamp = isoformat()
        config = {
            "schema_version": SCHEMA_VERSION,
            "target_domain": domain,
            "email_mode": args.email_mode,
            "crawl_mode": args.crawl_mode,
            "run_mode": args.run_mode,
            "execution": execution,
            "created_at": timestamp,
            "updated_at": timestamp,
            "email_limits": {
                "max_rolling_24h": EMAIL_LIMIT_24H,
                "max_rolling_7d": EMAIL_LIMIT_7D,
                "followup_wait_days": FOLLOWUP_WAIT_DAYS,
                "max_followups": 1,
            },
        }
        atomic_write_json(path, config)
        created = True

    db_path = database_path(args.workspace, domain)
    connection = sqlite3.connect(db_path, timeout=10)
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    log_event(connection, "state_initialized" if created else "state_reconciled", {"target_domain": domain})
    connection.commit()
    connection.close()
    emit(
        {
            "ok": True,
            "created": created,
            "state_directory": str(directory),
            "privacy_gitignore": str(privacy_ignore),
            "config": config,
        }
    )


def command_status(args: argparse.Namespace) -> None:
    config, connection = context(args)
    opportunity_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status, COUNT(*) AS count FROM opportunities GROUP BY status")
    }
    email_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status, COUNT(*) AS count FROM emails GROUP BY status")
    }
    backlink_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status, COUNT(*) AS count FROM backlinks GROUP BY status")
    }
    disavow_counts = {
        row["status"]: row["count"]
        for row in connection.execute("SELECT status, COUNT(*) AS count FROM disavow_candidates GROUP BY status")
    }
    pending = [
        row_dict(row)
        for row in connection.execute(
            "SELECT id, opportunity_id, recipient, subject, kind, status, proposed_at, approval_expires_at "
            "FROM emails WHERE status IN ('proposed', 'approved') ORDER BY proposed_at LIMIT 25"
        )
    ]
    latest_baseline_row = connection.execute(
        "SELECT * FROM baselines ORDER BY captured_at DESC LIMIT 1"
    ).fetchone()
    latest_baseline = row_dict(latest_baseline_row) if latest_baseline_row else None
    if latest_baseline:
        latest_baseline["metrics"] = json.loads(latest_baseline.pop("metrics_json"))
    emit(
        {
            "ok": True,
            "state_directory": str(state_dir(args.workspace, args.domain)),
            "config": config,
            "opportunities": opportunity_counts,
            "emails": email_counts,
            "backlinks": backlink_counts,
            "disavow_candidates": disavow_counts,
            "baseline_snapshots": connection.execute("SELECT COUNT(*) FROM baselines").fetchone()[0],
            "latest_baseline": latest_baseline,
            "suppressions": connection.execute("SELECT COUNT(*) FROM suppressions").fetchone()[0],
            "pending_email_proposals": pending,
        }
    )
    connection.close()


def command_baseline_record(args: argparse.Namespace) -> None:
    _, connection = context(args)
    metrics_path = Path(args.metrics_file).expanduser()
    try:
        if metrics_path.stat().st_size > 1_000_000:
            raise ControlError("Baseline metrics file must be 1 MB or smaller")
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ControlError(f"Could not read baseline metrics file: {metrics_path}") from exc
    except json.JSONDecodeError as exc:
        raise ControlError("Baseline metrics file must contain valid JSON") from exc
    if not isinstance(metrics, dict):
        raise ControlError("Baseline metrics JSON must be an object")
    captured_at = args.captured_at or isoformat()
    try:
        parse_time(captured_at)
    except ValueError as exc:
        raise ControlError("Baseline captured time must be an ISO 8601 timestamp") from exc
    baseline_id = generated_id("base")
    connection.execute(
        """
        INSERT INTO baselines (id, captured_at, source, coverage, metrics_json, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            baseline_id,
            captured_at,
            args.source,
            args.coverage,
            json.dumps(metrics, sort_keys=True, ensure_ascii=False),
            args.notes or "",
        ),
    )
    log_event(connection, "baseline_recorded", {"baseline_id": baseline_id, "source": args.source})
    connection.commit()
    connection.close()
    emit(
        {
            "ok": True,
            "baseline": {
                "id": baseline_id,
                "captured_at": captured_at,
                "source": args.source,
                "coverage": args.coverage,
                "metrics": metrics,
                "notes": args.notes or "",
            },
        }
    )


def command_disavow_candidate_add(args: argparse.Namespace) -> None:
    _, connection = context(args)
    source_url = args.source_url.strip() if args.source_url else None
    if source_url:
        parsed = urlsplit(source_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ControlError("Disavow candidate source URL must be an absolute HTTP(S) URL")
        derived_domain = normalize_domain(parsed.hostname)
    else:
        derived_domain = None
    if args.source_domain:
        source_domain = normalize_domain(args.source_domain)
        if derived_domain and source_domain != derived_domain:
            raise ControlError("Source URL hostname and source domain do not match")
    elif derived_domain:
        source_domain = derived_domain
    else:
        raise ControlError("Provide --source-url or --source-domain")
    seed = source_url or source_domain
    candidate_id = args.id or generated_id("dsv", seed)
    timestamp = isoformat()
    existing = connection.execute(
        "SELECT created_at FROM disavow_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    created_at = existing["created_at"] if existing else timestamp
    connection.execute(
        """
        INSERT INTO disavow_candidates (
            id, source_url, source_domain, status, risk_evidence, pattern_evidence,
            acquisition_history, removal_status, manual_action_evidence, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_url = excluded.source_url,
            source_domain = excluded.source_domain,
            status = excluded.status,
            risk_evidence = excluded.risk_evidence,
            pattern_evidence = excluded.pattern_evidence,
            acquisition_history = excluded.acquisition_history,
            removal_status = excluded.removal_status,
            manual_action_evidence = excluded.manual_action_evidence,
            updated_at = excluded.updated_at
        """,
        (
            candidate_id,
            source_url,
            source_domain,
            args.status,
            args.risk_evidence,
            args.pattern_evidence,
            args.acquisition_history,
            args.removal_status,
            args.manual_action_evidence,
            created_at,
            timestamp,
        ),
    )
    log_event(connection, "disavow_candidate_upserted", {"candidate_id": candidate_id, "status": args.status})
    connection.commit()
    row = connection.execute("SELECT * FROM disavow_candidates WHERE id = ?", (candidate_id,)).fetchone()
    connection.close()
    emit({"ok": True, "disavow_candidate": row_dict(row)})


def command_disavow_candidate_list(args: argparse.Namespace) -> None:
    _, connection = context(args)
    parameters: list[Any] = []
    query = "SELECT * FROM disavow_candidates"
    if args.status:
        query += " WHERE status = ?"
        parameters.append(args.status)
    query += " ORDER BY updated_at DESC LIMIT ?"
    parameters.append(args.limit)
    rows = [row_dict(row) for row in connection.execute(query, parameters)]
    connection.close()
    emit({"ok": True, "count": len(rows), "disavow_candidates": rows})


def command_mode_set(args: argparse.Namespace) -> None:
    if not args.confirmed_by_user:
        raise ControlError("Mode changes require --confirmed-by-user after explicit user confirmation")
    config, connection = context(args)
    previous = config["email_mode"]
    config["email_mode"] = args.email_mode
    config["updated_at"] = isoformat()
    atomic_write_json(config_path(args.workspace, args.domain), config)
    log_event(connection, "email_mode_changed", {"from": previous, "to": args.email_mode, "confirmed_by_user": True})
    connection.commit()
    connection.close()
    emit({"ok": True, "previous_email_mode": previous, "email_mode": args.email_mode})


def command_opportunity_add(args: argparse.Namespace) -> None:
    config, connection = context(args)
    opportunity_domain = normalize_domain(args.opportunity_domain)
    target_url = require_url_on_domain(
        args.target_url,
        config["target_domain"],
        "Opportunity target URL",
    )
    for name in (
        "relevance",
        "quality",
        "audience_value",
        "viability",
        "target_fit",
        "cost_efficiency",
        "difficulty",
        "risk",
    ):
        if not 0 <= getattr(args, name) <= 5:
            raise ControlError(f"{name} must be between 0 and 5")
    if args.risk >= 4 and args.status != "rejected":
        raise ControlError("Risk 4–5 opportunities must be stored as rejected")
    priority_score = (
        args.relevance * 5
        + args.quality * 4
        + args.audience_value * 2
        + args.viability * 4
        + args.target_fit * 2
        + args.cost_efficiency
        + (5 - args.difficulty) * 2
    )
    seed = "|".join((opportunity_domain, args.source_url or "", target_url, args.category))
    opportunity_id = args.id or generated_id("opp", seed)
    timestamp = isoformat()
    existing = connection.execute("SELECT created_at FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    created_at = existing["created_at"] if existing else timestamp
    connection.execute(
        """
        INSERT INTO opportunities (
            id, opportunity_domain, source_url, target_url, category, status,
            priority_score, relevance, quality, audience_value, viability, target_fit,
            cost_efficiency, difficulty, risk, cost, evidence, contact_route,
            next_action, next_check_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            opportunity_domain = excluded.opportunity_domain,
            source_url = excluded.source_url,
            target_url = excluded.target_url,
            category = excluded.category,
            status = excluded.status,
            priority_score = excluded.priority_score,
            relevance = excluded.relevance,
            quality = excluded.quality,
            audience_value = excluded.audience_value,
            viability = excluded.viability,
            target_fit = excluded.target_fit,
            cost_efficiency = excluded.cost_efficiency,
            difficulty = excluded.difficulty,
            risk = excluded.risk,
            cost = excluded.cost,
            evidence = excluded.evidence,
            contact_route = excluded.contact_route,
            next_action = excluded.next_action,
            next_check_at = excluded.next_check_at,
            updated_at = excluded.updated_at
        """,
        (
            opportunity_id,
            opportunity_domain,
            args.source_url,
            target_url,
            args.category,
            args.status,
            priority_score,
            args.relevance,
            args.quality,
            args.audience_value,
            args.viability,
            args.target_fit,
            args.cost_efficiency,
            args.difficulty,
            args.risk,
            args.cost,
            args.evidence,
            args.contact_route,
            args.next_action,
            args.next_check_at,
            created_at,
            timestamp,
        ),
    )
    log_event(connection, "opportunity_upserted", {"opportunity_id": opportunity_id, "status": args.status})
    connection.commit()
    row = require_opportunity(connection, opportunity_id)
    connection.close()
    emit({"ok": True, "opportunity": row_dict(row)})


def command_opportunity_list(args: argparse.Namespace) -> None:
    _, connection = context(args)
    parameters: list[Any] = []
    query = "SELECT * FROM opportunities"
    if args.status:
        query += " WHERE status = ?"
        parameters.append(args.status)
    query += " ORDER BY priority_score DESC, updated_at DESC LIMIT ?"
    parameters.append(args.limit)
    records = [row_dict(row) for row in connection.execute(query, parameters)]
    connection.close()
    emit({"ok": True, "count": len(records), "opportunities": records})


def command_attempt_log(args: argparse.Namespace) -> None:
    _, connection = context(args)
    require_opportunity(connection, args.opportunity_id)
    timestamp = args.occurred_at or isoformat()
    cursor = connection.execute(
        """
        INSERT INTO attempts (
            opportunity_id, occurred_at, actor, action_type, channel, outcome, detail, next_step
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            args.opportunity_id,
            timestamp,
            args.actor,
            args.action_type,
            args.channel,
            args.outcome,
            args.detail,
            args.next_step,
        ),
    )
    normalized_outcome = args.outcome.strip().lower()
    mapped_status = OUTCOME_STATUS.get(normalized_outcome)
    if mapped_status:
        connection.execute(
            "UPDATE opportunities SET status = ?, updated_at = ? WHERE id = ?",
            (mapped_status, isoformat(), args.opportunity_id),
        )
    suppression_label = AUTO_SUPPRESSION_OUTCOMES.get(normalized_outcome)
    if suppression_label:
        latest_email = connection.execute(
            """
            SELECT recipient FROM emails
            WHERE opportunity_id = ? AND status = 'sent'
            ORDER BY sent_at DESC LIMIT 1
            """,
            (args.opportunity_id,),
        ).fetchone()
        if latest_email:
            connection.execute(
                """
                INSERT INTO suppressions (suppression_key, reason, created_at) VALUES (?, ?, ?)
                ON CONFLICT(suppression_key) DO UPDATE SET reason = excluded.reason
                """,
                (latest_email["recipient"], suppression_label, isoformat()),
            )
    log_event(connection, "attempt_logged", {"opportunity_id": args.opportunity_id, "attempt_id": cursor.lastrowid})
    connection.commit()
    connection.close()
    emit({"ok": True, "attempt_id": cursor.lastrowid, "opportunity_id": args.opportunity_id})


def command_email_propose(args: argparse.Namespace) -> None:
    config, connection = context(args)
    opportunity = require_opportunity(connection, args.opportunity_id)
    if opportunity["status"] in ("rejected", "declined", "verified", "lost"):
        raise ControlError(f"Cannot propose outreach for opportunity in status {opportunity['status']}")
    if args.kind in ("initial", "followup") and opportunity["status"] in (
        "response-received",
        "placement-promised",
    ):
        raise ControlError(f"Cannot propose {args.kind} outreach after status {opportunity['status']}")
    if opportunity["risk"] >= 4:
        raise ControlError("Cannot propose outreach for a risk 4–5 opportunity")
    recipient = normalize_email(args.recipient)
    subject = args.subject.strip()
    if not subject or "\n" in subject or "\r" in subject or len(subject) > MAX_SUBJECT_CHARS:
        raise ControlError(f"Subject must be one line and at most {MAX_SUBJECT_CHARS} characters")
    body_path = Path(args.body_file).expanduser()
    try:
        body = body_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ControlError(f"Could not read body file: {body_path}") from exc
    word_count = len(WORD_RE.findall(body))
    if not body or word_count > MAX_BODY_WORDS:
        raise ControlError(f"Body must contain 1–{MAX_BODY_WORDS} words; found {word_count}")
    if len(body.encode("utf-8")) > 4000:
        raise ControlError("Body is too large")
    body_urls = extract_http_urls(body)
    if len(body_urls) > 2:
        raise ControlError("Body may contain at most two URLs")
    body_url_domains = [normalize_http_url(url, "Email body URL")[1] for url in body_urls]
    if args.kind in ("initial", "followup"):
        target_domain = config["target_domain"]
        if not any(domain_matches(hostname, target_domain) for hostname in body_url_domains):
            raise ControlError(
                f"{args.kind.capitalize()} outreach must include a URL on authorized target domain {target_domain}"
            )
        allowed_domains = (target_domain, opportunity["opportunity_domain"])
        if any(
            not any(domain_matches(hostname, allowed) for allowed in allowed_domains)
            for hostname in body_url_domains
        ):
            raise ControlError(
                "Initial and follow-up outreach URLs may only use the authorized target or opportunity domain"
            )
    if HTML_RE.search(body):
        raise ControlError("Body must be plain text, not HTML")
    proposal_id = generated_id("eml")
    timestamp = isoformat()
    digest = message_hash(recipient, subject, body, args.opportunity_id, args.kind)
    connection.execute(
        """
        INSERT INTO emails (
            id, opportunity_id, recipient, subject, body, body_sha256, kind, status, proposed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
        """,
        (proposal_id, args.opportunity_id, recipient, subject, body, digest, args.kind, timestamp),
    )
    connection.execute(
        "UPDATE opportunities SET status = 'outreach-proposed', updated_at = ? WHERE id = ?",
        (timestamp, args.opportunity_id),
    )
    log_event(connection, "email_proposed", {"email_id": proposal_id, "opportunity_id": args.opportunity_id})
    connection.commit()
    connection.close()
    emit(
        {
            "ok": True,
            "proposal": {
                "id": proposal_id,
                "opportunity_id": args.opportunity_id,
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "kind": args.kind,
                "body_sha256": digest,
                "word_count": word_count,
                "status": "proposed",
            },
        }
    )


def command_email_show(args: argparse.Namespace) -> None:
    _, connection = context(args)
    row = connection.execute("SELECT * FROM emails WHERE id = ?", (args.id,)).fetchone()
    connection.close()
    if row is None:
        raise ControlError(f"Unknown email proposal: {args.id}")
    emit({"ok": True, "proposal": row_dict(row)})


def command_email_approve(args: argparse.Namespace) -> None:
    if not args.confirmed_by_user:
        raise ControlError("Approval requires --confirmed-by-user after the user approves this exact proposal")
    config, connection = context(args)
    if config["email_mode"] != "restricted":
        raise ControlError("email-approve is only used in restricted mode")
    row = connection.execute("SELECT * FROM emails WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ControlError(f"Unknown email proposal: {args.id}")
    if row["status"] != "proposed":
        raise ControlError(f"Proposal cannot be approved from status {row['status']}")
    approved_at = now_utc()
    expires_at = approved_at + timedelta(hours=APPROVAL_HOURS)
    connection.execute(
        "UPDATE emails SET status = 'approved', approved_at = ?, approval_expires_at = ? WHERE id = ?",
        (isoformat(approved_at), isoformat(expires_at), args.id),
    )
    connection.execute(
        "UPDATE opportunities SET status = 'approval-pending', updated_at = ? WHERE id = ?",
        (isoformat(approved_at), row["opportunity_id"]),
    )
    log_event(connection, "email_approved", {"email_id": args.id, "confirmed_by_user": True})
    connection.commit()
    connection.close()
    emit({"ok": True, "email_id": args.id, "approved_at": isoformat(approved_at), "expires_at": isoformat(expires_at)})


def suppression_reason(connection: sqlite3.Connection, recipient: str) -> Optional[str]:
    domain_key = f"@{recipient.rsplit('@', 1)[1]}"
    row = connection.execute(
        "SELECT suppression_key, reason FROM suppressions WHERE suppression_key IN (?, ?)",
        (recipient, domain_key),
    ).fetchone()
    if row is None:
        return None
    return f"{row['suppression_key']}: {row['reason']}"


def sent_count(connection: sqlite3.Connection, since: datetime) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM emails WHERE status = 'sent' AND sent_at >= ?",
        (isoformat(since),),
    ).fetchone()[0]


def active_reservations(connection: sqlite3.Connection, since: datetime, at: datetime) -> int:
    return connection.execute(
        """
        SELECT COUNT(*)
        FROM send_tokens AS t
        JOIN emails AS e ON e.id = t.email_id
        WHERE t.consumed_at IS NULL
          AND t.expires_at > ?
          AND t.issued_at >= ?
          AND e.status != 'sent'
        """,
        (isoformat(at), isoformat(since)),
    ).fetchone()[0]


def command_email_authorize(args: argparse.Namespace) -> None:
    config, connection = context(args)
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute("SELECT * FROM emails WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ControlError(f"Unknown email proposal: {args.id}")
    if config["email_mode"] == "read-only":
        raise ControlError("Read-only mode never authorizes outbound email")
    if row["status"] not in ("proposed", "approved"):
        raise ControlError(f"Proposal cannot be authorized from status {row['status']}")

    opportunity = require_opportunity(connection, row["opportunity_id"])
    if opportunity["status"] in ("rejected", "declined", "verified", "lost"):
        raise ControlError(f"Opportunity status {opportunity['status']} blocks outbound email")
    if row["kind"] in ("initial", "followup") and opportunity["status"] in (
        "response-received",
        "placement-promised",
    ):
        raise ControlError(f"Opportunity status {opportunity['status']} blocks {row['kind']} outreach")
    if opportunity["risk"] >= 4:
        raise ControlError("Risk 4–5 opportunities can never be authorized")
    if opportunity["risk"] == 3 and config["email_mode"] == "unrestricted":
        raise ControlError("Risk 3 requires exact human review; switch to restricted mode and create a new proposal")

    reason = suppression_reason(connection, row["recipient"])
    if reason:
        raise ControlError(f"Recipient is suppressed ({reason})")

    expected_hash = message_hash(row["recipient"], row["subject"], row["body"], row["opportunity_id"], row["kind"])
    if expected_hash != row["body_sha256"]:
        raise ControlError("Proposal content no longer matches its approval hash")

    at = now_utc()
    if config["email_mode"] == "restricted":
        if row["status"] != "approved" or not row["approval_expires_at"]:
            raise ControlError("Restricted mode requires exact-message approval")
        if parse_time(row["approval_expires_at"]) <= at:
            raise ControlError("Restricted-mode approval has expired; create a new proposal")

    existing_token = connection.execute(
        """
        SELECT token, expires_at FROM send_tokens
        WHERE email_id = ? AND consumed_at IS NULL AND expires_at > ?
        ORDER BY issued_at DESC LIMIT 1
        """,
        (args.id, isoformat(at)),
    ).fetchone()
    if existing_token is not None:
        connection.rollback()
        connection.close()
        emit(
            {
                "ok": True,
                "email_id": args.id,
                "send_token": existing_token["token"],
                "expires_at": existing_token["expires_at"],
                "recipient": row["recipient"],
                "subject": row["subject"],
                "body": row["body"],
                "reused_active_token": True,
            }
        )
        return

    count_24h = sent_count(connection, at - timedelta(hours=24)) + active_reservations(
        connection, at - timedelta(hours=24), at
    )
    count_7d = sent_count(connection, at - timedelta(days=7)) + active_reservations(
        connection, at - timedelta(days=7), at
    )
    if count_24h >= EMAIL_LIMIT_24H:
        raise ControlError(f"Rolling 24-hour outbound cap reached ({EMAIL_LIMIT_24H})")
    if count_7d >= EMAIL_LIMIT_7D:
        raise ControlError(f"Rolling 7-day outbound cap reached ({EMAIL_LIMIT_7D})")

    if row["kind"] == "initial":
        duplicate = connection.execute(
            """
            SELECT id FROM emails
            WHERE status = 'sent' AND kind = 'initial' AND recipient = ? AND opportunity_id = ?
            LIMIT 1
            """,
            (row["recipient"], row["opportunity_id"]),
        ).fetchone()
        if duplicate:
            raise ControlError(f"An initial email was already sent for this recipient/opportunity ({duplicate['id']})")
    elif row["kind"] == "followup":
        blocking_attempt = connection.execute(
            f"""
            SELECT id, outcome FROM attempts
            WHERE opportunity_id = ? AND lower(outcome) IN ({','.join('?' for _ in FOLLOWUP_BLOCKING_OUTCOMES)})
            ORDER BY occurred_at DESC LIMIT 1
            """,
            (row["opportunity_id"], *FOLLOWUP_BLOCKING_OUTCOMES),
        ).fetchone()
        if blocking_attempt:
            raise ControlError(
                f"Follow-up blocked by recorded outcome {blocking_attempt['outcome']} (attempt {blocking_attempt['id']})"
            )
        previous = connection.execute(
            """
            SELECT id, sent_at FROM emails
            WHERE status = 'sent' AND recipient = ? AND opportunity_id = ?
            ORDER BY sent_at DESC LIMIT 1
            """,
            (row["recipient"], row["opportunity_id"]),
        ).fetchone()
        if previous is None:
            raise ControlError("A follow-up requires a previously sent message for the same recipient/opportunity")
        prior_followup = connection.execute(
            """
            SELECT id FROM emails
            WHERE status = 'sent' AND kind = 'followup' AND recipient = ? AND opportunity_id = ?
            LIMIT 1
            """,
            (row["recipient"], row["opportunity_id"]),
        ).fetchone()
        if prior_followup:
            raise ControlError(f"The single allowed follow-up was already sent ({prior_followup['id']})")
        eligible_at = parse_time(previous["sent_at"]) + timedelta(days=FOLLOWUP_WAIT_DAYS)
        if eligible_at > at:
            raise ControlError(f"Follow-up is too early; eligible after {isoformat(eligible_at)}")
    else:
        recent_replies = connection.execute(
            """
            SELECT COUNT(*) FROM emails
            WHERE status = 'sent' AND kind = 'reply' AND recipient = ? AND opportunity_id = ? AND sent_at >= ?
            """,
            (row["recipient"], row["opportunity_id"], isoformat(at - timedelta(days=7))),
        ).fetchone()[0]
        if recent_replies >= 3:
            raise ControlError("Reply limit reached for this recipient/opportunity; request user review")

    token = generated_id("snd")
    expires_at = at + timedelta(minutes=TOKEN_MINUTES)
    connection.execute(
        "INSERT INTO send_tokens (token, email_id, issued_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, args.id, isoformat(at), isoformat(expires_at)),
    )
    log_event(
        connection,
        "email_send_authorized",
        {"email_id": args.id, "expires_at": isoformat(expires_at)},
    )
    connection.commit()
    connection.close()
    emit(
        {
            "ok": True,
            "email_id": args.id,
            "send_token": token,
            "expires_at": isoformat(expires_at),
            "recipient": row["recipient"],
            "subject": row["subject"],
            "body": row["body"],
            "limits_after_reservation": {"rolling_24h": count_24h + 1, "rolling_7d": count_7d + 1},
        }
    )


def command_email_record_sent(args: argparse.Namespace) -> None:
    _, connection = context(args)
    connection.execute("BEGIN IMMEDIATE")
    token = connection.execute("SELECT * FROM send_tokens WHERE token = ?", (args.token,)).fetchone()
    if token is None or token["email_id"] != args.id:
        raise ControlError("Send token is invalid for this proposal")
    if token["consumed_at"] is not None:
        raise ControlError("Send token was already consumed")
    at = now_utc()
    if parse_time(token["expires_at"]) <= at:
        raise ControlError("Send token has expired")
    email = connection.execute("SELECT * FROM emails WHERE id = ?", (args.id,)).fetchone()
    if email is None:
        raise ControlError(f"Unknown email proposal: {args.id}")
    if email["status"] == "sent":
        raise ControlError("Proposal is already recorded as sent")
    timestamp = isoformat(at)
    connection.execute("UPDATE send_tokens SET consumed_at = ? WHERE token = ?", (timestamp, args.token))
    connection.execute(
        "UPDATE emails SET status = 'sent', sent_at = ?, provider_message_id = ? WHERE id = ?",
        (timestamp, args.provider_message_id, args.id),
    )
    connection.execute(
        "UPDATE opportunities SET status = 'outreach-sent', updated_at = ? WHERE id = ?",
        (timestamp, email["opportunity_id"]),
    )
    log_event(
        connection,
        "email_sent_recorded",
        {"email_id": args.id, "provider_message_id": args.provider_message_id or "unknown"},
    )
    connection.commit()
    connection.close()
    emit({"ok": True, "email_id": args.id, "sent_at": timestamp, "token_consumed": True})


def command_email_cancel(args: argparse.Namespace) -> None:
    _, connection = context(args)
    row = connection.execute("SELECT * FROM emails WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ControlError(f"Unknown email proposal: {args.id}")
    if row["status"] == "sent":
        raise ControlError("A sent email cannot be cancelled")
    timestamp = isoformat()
    connection.execute("UPDATE emails SET status = 'cancelled', cancelled_at = ? WHERE id = ?", (timestamp, args.id))
    connection.execute("UPDATE send_tokens SET consumed_at = ? WHERE email_id = ? AND consumed_at IS NULL", (timestamp, args.id))
    log_event(connection, "email_cancelled", {"email_id": args.id, "reason": args.reason})
    connection.commit()
    connection.close()
    emit({"ok": True, "email_id": args.id, "status": "cancelled"})


def command_suppress(args: argparse.Namespace) -> None:
    _, connection = context(args)
    key = normalize_suppression(args.recipient)
    timestamp = isoformat()
    connection.execute(
        """
        INSERT INTO suppressions (suppression_key, reason, created_at) VALUES (?, ?, ?)
        ON CONFLICT(suppression_key) DO UPDATE SET reason = excluded.reason
        """,
        (key, args.reason, timestamp),
    )
    log_event(connection, "recipient_suppressed", {"suppression_key": key, "reason": args.reason})
    connection.commit()
    connection.close()
    emit({"ok": True, "suppression_key": key, "reason": args.reason})


def command_backlink_record(args: argparse.Namespace) -> None:
    config, connection = context(args)
    if args.opportunity_id:
        require_opportunity(connection, args.opportunity_id)
    source_url, _ = normalize_http_url(args.source_url, "Backlink source URL")
    target_url = require_url_on_domain(
        args.target_url,
        config["target_domain"],
        "Backlink target URL",
    )
    if args.status == "verified" and not 200 <= args.http_status < 400:
        raise ControlError("A verified backlink requires a successful source-page HTTP status")
    seed = f"{source_url}|{target_url}"
    backlink_id = args.id or generated_id("lnk", seed)
    at = isoformat()
    existing = connection.execute("SELECT first_verified_at FROM backlinks WHERE id = ?", (backlink_id,)).fetchone()
    first_verified_at = existing["first_verified_at"] if existing else (at if args.status == "verified" else None)
    connection.execute(
        """
        INSERT INTO backlinks (
            id, opportunity_id, source_url, target_url, anchor_text, rel_values,
            http_status, status, first_verified_at, last_checked_at, evidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            opportunity_id = excluded.opportunity_id,
            source_url = excluded.source_url,
            target_url = excluded.target_url,
            anchor_text = excluded.anchor_text,
            rel_values = excluded.rel_values,
            http_status = excluded.http_status,
            status = excluded.status,
            last_checked_at = excluded.last_checked_at,
            evidence = excluded.evidence
        """,
        (
            backlink_id,
            args.opportunity_id,
            source_url,
            target_url,
            args.anchor_text,
            args.rel_values,
            args.http_status,
            args.status,
            first_verified_at,
            at,
            args.evidence,
        ),
    )
    if args.opportunity_id and args.status in ("verified", "lost"):
        connection.execute(
            "UPDATE opportunities SET status = ?, updated_at = ? WHERE id = ?",
            (args.status, at, args.opportunity_id),
        )
    log_event(connection, "backlink_recorded", {"backlink_id": backlink_id, "status": args.status})
    connection.commit()
    row = connection.execute("SELECT * FROM backlinks WHERE id = ?", (backlink_id,)).fetchone()
    connection.close()
    emit({"ok": True, "backlink": row_dict(row)})


def add_state_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default=".", help="Workspace that owns .backlink-hunter state (default: current directory)")
    parser.add_argument("--domain", required=True, help="Authorized target domain")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"backlink-hunter-state {SCHEMA_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize durable state for one target domain")
    add_state_args(init_parser)
    init_parser.add_argument("--email-mode", choices=EMAIL_MODES, required=True)
    init_parser.add_argument("--crawl-mode", choices=CRAWL_MODES, required=True)
    init_parser.add_argument("--run-mode", choices=RUN_MODES, required=True)
    init_parser.add_argument("--goal", help="Optional measurable outcome; goal mode defaults to one verified quality backlink")
    init_parser.add_argument("--cadence", help="Loop interval or human-readable schedule cadence")
    init_parser.add_argument("--timezone", help="IANA timezone for schedules, such as Etc/UTC")
    init_parser.set_defaults(handler=command_init)

    status_parser = subparsers.add_parser("status", help="Show campaign and control status")
    add_state_args(status_parser)
    status_parser.set_defaults(handler=command_status)

    baseline_parser = subparsers.add_parser("baseline-record", help="Store a timestamped backlink baseline snapshot")
    add_state_args(baseline_parser)
    baseline_parser.add_argument("--captured-at", help="ISO 8601 timestamp; defaults to now")
    baseline_parser.add_argument("--source", required=True, help="SEO provider, user export, or web-research source")
    baseline_parser.add_argument(
        "--coverage",
        choices=("exact", "provider-estimate", "partial", "unknown"),
        required=True,
    )
    baseline_parser.add_argument("--metrics-file", required=True, help="JSON object containing source-specific metrics")
    baseline_parser.add_argument("--notes")
    baseline_parser.set_defaults(handler=command_baseline_record)

    disavow_parser = subparsers.add_parser(
        "disavow-candidate-add",
        help="Store evidence for human disavow review; never uploads a list",
    )
    add_state_args(disavow_parser)
    disavow_parser.add_argument("--id")
    disavow_parser.add_argument("--source-url")
    disavow_parser.add_argument("--source-domain")
    disavow_parser.add_argument("--status", choices=DISAVOW_STATUSES, default="candidate")
    disavow_parser.add_argument("--risk-evidence", required=True)
    disavow_parser.add_argument("--pattern-evidence", required=True)
    disavow_parser.add_argument("--acquisition-history", default="unknown")
    disavow_parser.add_argument("--removal-status", default="not-attempted")
    disavow_parser.add_argument("--manual-action-evidence", default="none")
    disavow_parser.set_defaults(handler=command_disavow_candidate_add)

    disavow_list_parser = subparsers.add_parser(
        "disavow-candidate-list",
        help="List evidence for human review",
    )
    add_state_args(disavow_list_parser)
    disavow_list_parser.add_argument("--status", choices=DISAVOW_STATUSES)
    disavow_list_parser.add_argument("--limit", type=int, default=50)
    disavow_list_parser.set_defaults(handler=command_disavow_candidate_list)

    mode_parser = subparsers.add_parser("mode-set", help="Change email mode after explicit user confirmation")
    add_state_args(mode_parser)
    mode_parser.add_argument("--email-mode", choices=EMAIL_MODES, required=True)
    mode_parser.add_argument("--confirmed-by-user", action="store_true")
    mode_parser.set_defaults(handler=command_mode_set)

    opportunity_parser = subparsers.add_parser("opportunity-add", help="Add or update a normalized opportunity")
    add_state_args(opportunity_parser)
    opportunity_parser.add_argument("--id")
    opportunity_parser.add_argument("--opportunity-domain", required=True)
    opportunity_parser.add_argument("--source-url")
    opportunity_parser.add_argument("--target-url", required=True)
    opportunity_parser.add_argument("--category", required=True)
    opportunity_parser.add_argument("--status", choices=OPPORTUNITY_STATUSES, default="discovered")
    opportunity_parser.add_argument("--relevance", type=int, required=True)
    opportunity_parser.add_argument("--quality", type=int, required=True)
    opportunity_parser.add_argument("--audience-value", type=int, required=True)
    opportunity_parser.add_argument("--viability", type=int, required=True)
    opportunity_parser.add_argument("--target-fit", type=int, required=True)
    opportunity_parser.add_argument("--cost-efficiency", type=int, required=True)
    opportunity_parser.add_argument("--difficulty", type=int, required=True)
    opportunity_parser.add_argument("--risk", type=int, required=True)
    opportunity_parser.add_argument("--cost", required=True)
    opportunity_parser.add_argument("--evidence", required=True)
    opportunity_parser.add_argument("--contact-route")
    opportunity_parser.add_argument("--next-action")
    opportunity_parser.add_argument("--next-check-at")
    opportunity_parser.set_defaults(handler=command_opportunity_add)

    list_parser = subparsers.add_parser("opportunity-list", help="List prioritized opportunities")
    add_state_args(list_parser)
    list_parser.add_argument("--status", choices=OPPORTUNITY_STATUSES)
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(handler=command_opportunity_list)

    attempt_parser = subparsers.add_parser("attempt-log", help="Append an immutable opportunity attempt")
    add_state_args(attempt_parser)
    attempt_parser.add_argument("--opportunity-id", required=True)
    attempt_parser.add_argument("--occurred-at")
    attempt_parser.add_argument("--actor", required=True)
    attempt_parser.add_argument("--action-type", required=True)
    attempt_parser.add_argument("--channel", required=True)
    attempt_parser.add_argument("--outcome", required=True)
    attempt_parser.add_argument("--detail", required=True)
    attempt_parser.add_argument("--next-step")
    attempt_parser.set_defaults(handler=command_attempt_log)

    propose_parser = subparsers.add_parser("email-propose", help="Store an exact outbound email proposal")
    add_state_args(propose_parser)
    propose_parser.add_argument("--opportunity-id", required=True)
    propose_parser.add_argument("--recipient", required=True)
    propose_parser.add_argument("--subject", required=True)
    propose_parser.add_argument("--body-file", required=True)
    propose_parser.add_argument("--kind", choices=EMAIL_KINDS, default="initial")
    propose_parser.set_defaults(handler=command_email_propose)

    show_parser = subparsers.add_parser("email-show", help="Show the exact stored proposal")
    add_state_args(show_parser)
    show_parser.add_argument("--id", required=True)
    show_parser.set_defaults(handler=command_email_show)

    approve_parser = subparsers.add_parser("email-approve", help="Record exact-message approval in restricted mode")
    add_state_args(approve_parser)
    approve_parser.add_argument("--id", required=True)
    approve_parser.add_argument("--confirmed-by-user", action="store_true")
    approve_parser.set_defaults(handler=command_email_approve)

    authorize_parser = subparsers.add_parser("email-authorize", help="Apply controls and issue a one-time send token")
    add_state_args(authorize_parser)
    authorize_parser.add_argument("--id", required=True)
    authorize_parser.set_defaults(handler=command_email_authorize)

    sent_parser = subparsers.add_parser("email-record-sent", help="Consume a token immediately after the host sends")
    add_state_args(sent_parser)
    sent_parser.add_argument("--id", required=True)
    sent_parser.add_argument("--token", required=True)
    sent_parser.add_argument("--provider-message-id")
    sent_parser.set_defaults(handler=command_email_record_sent)

    cancel_parser = subparsers.add_parser("email-cancel", help="Cancel an unsent proposal and its active tokens")
    add_state_args(cancel_parser)
    cancel_parser.add_argument("--id", required=True)
    cancel_parser.add_argument("--reason", required=True)
    cancel_parser.set_defaults(handler=command_email_cancel)

    suppress_parser = subparsers.add_parser("suppress", help="Suppress an exact address or @domain")
    add_state_args(suppress_parser)
    suppress_parser.add_argument("--recipient", required=True, help="Exact address or @example.com")
    suppress_parser.add_argument("--reason", required=True)
    suppress_parser.set_defaults(handler=command_suppress)

    backlink_parser = subparsers.add_parser("backlink-record", help="Add or update web-verification evidence")
    add_state_args(backlink_parser)
    backlink_parser.add_argument("--id")
    backlink_parser.add_argument("--opportunity-id")
    backlink_parser.add_argument("--source-url", required=True)
    backlink_parser.add_argument("--target-url", required=True)
    backlink_parser.add_argument("--anchor-text")
    backlink_parser.add_argument("--rel-values", default="")
    backlink_parser.add_argument("--http-status", type=int, required=True)
    backlink_parser.add_argument("--status", choices=("verified", "changed", "lost"), required=True)
    backlink_parser.add_argument("--evidence", required=True)
    backlink_parser.set_defaults(handler=command_backlink_record)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except ControlError as exc:
        fail(str(exc))
    except sqlite3.Error as exc:
        fail(f"State database error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
