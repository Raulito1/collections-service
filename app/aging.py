# app/aging.py
"""QuickBooks A/R Aging report transformers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional


BucketKey = str


BUCKET_ORDER: List[BucketKey] = [
    "current",
    "1-20",
    "21-30",
    "31-45",
    "46-60",
    "61-90",
    "91+",
]

BUCKET_ACTION: Dict[BucketKey, str] = {
    "current": "No Action",
    "1-20": "Accounting Outreach",
    "21-30": "Accounting Outreach",
    "31-45": "CSM/AE Outreach",
    "46-60": "Management Escalation",
    "61-90": "Demand Letter",
    "91+": "Collections Review",
}


@dataclass
class AgingTransaction:
    customer: str
    doc_num: Optional[str]
    txn_type: str
    due_date: Optional[datetime]
    days_past_due: int
    bucket: BucketKey
    amount: Decimal


def _safe_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # QuickBooks sometimes omits zero padding; fallback to date only
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def _bucket_for_days(days: int) -> BucketKey:
    if days <= 0:
        return "current"
    if days <= 20:
        return "1-20"
    if days <= 30:
        return "21-30"
    if days <= 45:
        return "31-45"
    if days <= 60:
        return "46-60"
    if days <= 90:
        return "61-90"
    return "91+"


def _extract_transactions(report: Dict[str, Any]) -> Iterable[AgingTransaction]:
    header = report.get("Header", {})
    report_date: Optional[datetime] = None
    for opt in header.get("Option", []):
        if opt.get("Name") == "report_date" and opt.get("Value"):
            report_date = _parse_date(opt["Value"])
            break
    if report_date is None:
        report_date = datetime.utcnow()

    columns = report.get("Columns", {}).get("Column", [])
    # build index lookup to be safe
    idx = {col.get("MetaData", [{}])[0].get("Value"): i for i, col in enumerate(columns)}

    rows = report.get("Rows", {}).get("Row", [])
    for section in rows:
        if section.get("type") != "Section":
            continue
        nested = section.get("Rows", {}).get("Row", [])
        for entry in nested:
            if entry.get("type") != "Data":
                continue
            data = entry.get("ColData", [])

            def get_col(key: str) -> Optional[str]:
                position = idx.get(key)
                if position is None:
                    return None
                if position >= len(data):
                    return None
                return data[position].get("value")

            txn_type = get_col("txn_type") or ""
            customer = get_col("cust_name") or "Unknown"
            doc_num = get_col("doc_num")
            open_balance = _safe_decimal(get_col("subt_open_bal") or 0)
            if open_balance == 0:
                continue

            due_date = _parse_date(get_col("due_date"))
            days_past_due = 0
            if due_date is not None:
                days_past_due = (report_date.date() - due_date.date()).days

            bucket = _bucket_for_days(days_past_due)

            yield AgingTransaction(
                customer=customer,
                doc_num=doc_num,
                txn_type=txn_type,
                due_date=due_date,
                days_past_due=days_past_due,
                bucket=bucket,
                amount=open_balance,
            )


def simplify_ar_aging(report: Dict[str, Any]) -> Dict[str, Any]:
    """Convert QuickBooks report payload into collections-ready summary."""

    customers: Dict[str, Dict[str, Any]] = {}

    for txn in _extract_transactions(report):
        record = customers.setdefault(
            txn.customer,
            {
                "customer": txn.customer,
                "total_balance": Decimal("0"),
                "buckets": {key: Decimal("0") for key in BUCKET_ORDER},
                "positive_transactions": [],
                "credits": Decimal("0"),
            },
        )

        record["total_balance"] += txn.amount
        record["buckets"][txn.bucket] += txn.amount

        if txn.amount > 0:
            record["positive_transactions"].append(txn)
        else:
            record["credits"] += txn.amount

    output: List[Dict[str, Any]] = []

    bucket_rank = {bucket: idx for idx, bucket in enumerate(BUCKET_ORDER)}

    for record in customers.values():
        total_balance = record["total_balance"]
        if total_balance <= 0:
            continue

        # determine oldest bucket based on positive transactions only
        oldest_txn: Optional[AgingTransaction] = None
        for txn in record["positive_transactions"]:
            if oldest_txn is None:
                oldest_txn = txn
                continue
            if bucket_rank[txn.bucket] > bucket_rank[oldest_txn.bucket]:
                oldest_txn = txn
            elif bucket_rank[txn.bucket] == bucket_rank[oldest_txn.bucket]:
                if txn.days_past_due > oldest_txn.days_past_due:
                    oldest_txn = txn

        recommended_bucket: Optional[BucketKey] = None
        recommended_action: Optional[str] = None
        oldest_invoice_info: Optional[Dict[str, Any]] = None

        if oldest_txn is not None and oldest_txn.amount > 0:
            recommended_bucket = oldest_txn.bucket
            recommended_action = BUCKET_ACTION.get(recommended_bucket, "Review")
            oldest_invoice_info = {
                "doc_num": oldest_txn.doc_num,
                "txn_type": oldest_txn.txn_type,
                "due_date": oldest_txn.due_date.date().isoformat() if oldest_txn.due_date else None,
                "days_past_due": oldest_txn.days_past_due,
                "amount": float(oldest_txn.amount),
            }

        output.append(
            {
                "customer": record["customer"],
                "total_balance": float(total_balance),
                "buckets": {k: float(v) for k, v in record["buckets"].items()},
                "credits": float(record["credits"]),
                "recommended_bucket": recommended_bucket,
                "recommended_action": recommended_action,
                "oldest_invoice": oldest_invoice_info,
            }
        )

    # sort by largest total balance desc
    output.sort(key=lambda item: item["total_balance"], reverse=True)

    return {
        "generated_at": report.get("Header", {}).get("Time"),
        "rows": output,
    }


__all__ = ["simplify_ar_aging"]
