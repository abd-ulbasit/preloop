"""Measured weekly evidence, independent of model-generated prose."""

from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID
from sqlalchemy.orm import Session
from preloop.models.crud import crud_ai_model
from preloop.models.crud.weekly_report_evidence import usage_rows
from preloop.services.pricing_overrides import resolve_pricing_override


def report_usage(
    db: Session, *, account_id: UUID, start: datetime, end: datetime
) -> list[Any]:
    """Load reporting projections through CRUD."""
    return usage_rows(db, account_id=account_id, start=start, end=end)


def spend_evidence(
    rows: Sequence[Any],
    *,
    day: str,
    user_id: str | None,
    start: datetime,
    end: datetime,
    details: Mapping[str, Any],
    imported_rows: Sequence[Any] = (),
    imported_complete: bool = True,
) -> dict[str, Any]:
    """Build a daily chart and the outlier day's contributing sessions."""
    totals: dict[str, float] = {}
    sessions: dict[str, dict[str, Any]] = {}
    for row in rows:
        if user_id and str(row.user_id) != str(user_id):
            continue
        row_day = row.timestamp.date().isoformat()
        cost = float(row.estimated_cost or 0)
        totals[row_day] = totals.get(row_day, 0) + cost
        if row_day != day or not row.runtime_session_id:
            continue
        sid = str(row.runtime_session_id)
        if sid not in sessions:
            name = row.agent_name or row.runtime_principal_name or "Agent"
            started = row.started_at or row.timestamp
            sessions[sid] = {
                "runtime_session_id": sid,
                "title": row.title or f"{name} · {started:%d %b %H:%M} UTC",
                "agent_name": name,
                "started_at": started.replace(tzinfo=timezone.utc).isoformat(),
                "cost": 0.0,
                "href": f"/console/runtime-sessions?sessionId={sid}",
                "agent_href": f"/console/agents/{row.agent_id}"
                if row.agent_id
                else None,
                "flow_href": f"/console/flows/{row.flow_id}" if row.flow_id else None,
            }
        sessions[sid]["cost"] += cost
    imported_totals: dict[str, float] = {}
    for imported in imported_rows:
        if user_id and str(imported.user_id) != str(user_id):
            continue
        key = imported.day.isoformat()
        imported_totals[key] = imported_totals.get(key, 0) + float(imported.cost_usd)
    series = []
    current = min(start.date(), date.fromisoformat(day))
    end_day = max(
        end.date() + (timedelta(days=1) if end.time() != time.min else timedelta()),
        date.fromisoformat(day) + timedelta(days=1),
    )
    while current < end_day:
        key = current.isoformat()
        series.append(
            {
                "day": key,
                "cost": round(totals.get(key, 0) + imported_totals.get(key, 0), 6),
                "gateway_cost": round(totals.get(key, 0), 6),
                "imported_cost": round(imported_totals.get(key, 0), 6),
                "is_outlier": key == day,
            }
        )
        current += timedelta(days=1)
    return {
        "day": day,
        "cost": details.get("spend_usd", totals.get(day, 0)),
        "baseline": details.get("median_usd"),
        "ratio": details.get("multiple"),
        "daily_series": series,
        "contributing_sessions": sorted(
            sessions.values(),
            key=lambda item: (-item["cost"], item["runtime_session_id"]),
        )[:100],
        "series_source": "gateway_and_imports",
        "imported_complete": imported_complete,
        "contributing_sessions_count": len(sessions),
        "imported_cost": details.get("imported_usd", 0),
    }


def tool_evidence(
    db: Session, *, account_id: UUID, rows: Sequence[Any]
) -> dict[str, dict[str, Any]]:
    """Estimate each advertised tool separately; preserve MCP provenance.

    Costs use the request's input token tariff. Missing schema/pricing stays
    unknown, and estimates describe the observed window, not guaranteed savings.
    """
    from preloop.services.model_pricing import estimate_ai_model_usage_cost

    result: dict[str, dict[str, Any]] = {}
    prices: dict[tuple[Any, Any], float | None] = {}
    for row in rows:
        key = (row.ai_model_id, row.model_alias)
        if key not in prices:
            model = (
                crud_ai_model.get(db, id=row.ai_model_id) if row.ai_model_id else None
            )
            override = (
                resolve_pricing_override(
                    db,
                    account_id=str(account_id),
                    ai_model=model,
                    requested_alias=row.model_alias,
                )
                if model
                else None
            )
            prices[key] = (
                estimate_ai_model_usage_cost(
                    model,
                    prompt_tokens=1,
                    completion_tokens=0,
                    total_tokens=1,
                    pricing_override=override,
                )
                if model
                else None
            )
        price = 0.0 if row.cost_source == "subscription" else prices[key]
        for tool in row.tools_meta if isinstance(row.tools_meta, list) else []:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            name = tool["name"]
            item = result.setdefault(
                name,
                {
                    "name": name,
                    "request_count": 0,
                    "estimated_weekly_tokens": 0,
                    "estimated_weekly_savings": 0.0,
                    "capability": tool.get("description")
                    or name.split("__")[-1].replace("_", " "),
                    "per_session": {},
                },
            )
            sid = str(row.runtime_session_id) if row.runtime_session_id else ""
            measurement = item["per_session"].setdefault(
                sid, {"request_count": 0, "tokens": 0, "cost": 0.0}
            )
            measurement["request_count"] += 1
            tokens = tool.get("schema_tokens_estimate")
            known = (
                isinstance(tokens, (int, float))
                and not isinstance(tokens, bool)
                and tokens >= 0
            )
            measurement["tokens"] = (
                measurement["tokens"] + int(tokens)
                if known and measurement["tokens"] is not None
                else None
            )
            measurement["cost"] = (
                measurement["cost"] + tokens * price
                if known and price is not None and measurement["cost"] is not None
                else None
            )
    for item in result.values():
        measures = list(item["per_session"].values())
        item["request_count"] = sum(m["request_count"] for m in measures)
        item["session_ids"] = sorted(s for s in item["per_session"] if s)
        item["sessions_attached"] = len(item["session_ids"])
        item["estimated_weekly_tokens"] = (
            sum(m["tokens"] for m in measures)
            if all(m["tokens"] is not None for m in measures)
            else None
        )
        item["schema_tokens_per_request"] = (
            round(item["estimated_weekly_tokens"] / item["request_count"])
            if item["estimated_weekly_tokens"] is not None
            else None
        )
        item["estimated_weekly_savings"] = (
            sum(m["cost"] for m in measures)
            if all(m["cost"] is not None for m in measures)
            else None
        )
    return result
