# app/aging.py
"""QuickBooks A/R Aging report transformers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Set


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
    customer_ref: Optional[str]
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


def _clean_customer_name(raw: str) -> str:
    """Normalize formatting and remove trailing QuickBooks suffixes such as ':COMP'."""
    cleaned = raw.split(":", 1)[0]
    collapsed = " ".join(cleaned.split()).strip()
    return collapsed or raw.strip()


def _customer_key(name: str) -> str:
    """Canonicalize customer names for aggregation."""
    return " ".join(name.split()).casefold()


def _aggregate_customer_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse customer entries that share the same canonicalized name.

    This is useful when QuickBooks jobs (e.g., ``Customer:Job``) appear as distinct customers
    even though we want to treat them as one consolidated account for reporting.
    """
    aggregated: Dict[str, Dict[str, Any]] = {}
    for record in records:
        canonical = _customer_key(record["customer"])
        group = aggregated.get(canonical)
        if group is None:
            group = {
                "customer": record["customer"],
                "external_ref": None,
                "total_balance": Decimal("0"),
                "buckets": {bucket: Decimal("0") for bucket in BUCKET_ORDER},
                "positive_transactions": [],
                "credits": Decimal("0"),
                "external_refs": set(),
            }
            aggregated[canonical] = group

        group["total_balance"] += record["total_balance"]
        for bucket, amount in record["buckets"].items():
            group["buckets"][bucket] += amount
        group["positive_transactions"].extend(record["positive_transactions"])
        group["credits"] += record["credits"]

        ref = record.get("external_ref")
        if ref:
            refs: Set[str] = group["external_refs"]
            refs.add(ref)

        if group["customer"] != record["customer"]:
            if len(record["customer"]) > len(group["customer"]):
                group["customer"] = record["customer"]

    result: List[Dict[str, Any]] = []
    for group in aggregated.values():
        result.append(
            {
                "customer": group["customer"],
                "external_ref": None,
                "total_balance": group["total_balance"],
                "buckets": {bucket: amount for bucket, amount in group["buckets"].items()},
                "positive_transactions": list(group["positive_transactions"]),
                "credits": group["credits"],
                "external_refs": sorted(group["external_refs"]),
            }
        )
    return result


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

            def get_col_entry(key: str) -> Optional[Dict[str, Any]]:
                position = idx.get(key)
                if position is None or position >= len(data):
                    return None
                entry = data[position]
                if isinstance(entry, dict):
                    return entry
                return None

            def get_col_value(key: str) -> Optional[str]:
                col = get_col_entry(key)
                if not col:
                    return None
                value = col.get("value")
                return str(value) if value is not None else None

            def get_col_id(key: str) -> Optional[str]:
                col = get_col_entry(key)
                if not col:
                    return None
                ident = col.get("id")
                return str(ident) if ident is not None else None

            txn_type = get_col_value("txn_type") or ""
            customer_raw = get_col_value("cust_name") or "Unknown"
            customer = _clean_customer_name(customer_raw)
            doc_num = get_col_value("doc_num")
            customer_ref = get_col_id("cust_name")
            open_balance = _safe_decimal(get_col_value("subt_open_bal") or 0)
            if open_balance == 0:
                continue

            due_date = _parse_date(get_col_value("due_date"))
            days_past_due = 0
            if due_date is not None:
                days_past_due = (report_date.date() - due_date.date()).days

            bucket = _bucket_for_days(days_past_due)

            yield AgingTransaction(
                customer=customer,
                customer_ref=customer_ref,
                doc_num=doc_num,
                txn_type=txn_type,
                due_date=due_date,
                days_past_due=days_past_due,
                bucket=bucket,
                amount=open_balance,
            )


def simplify_ar_aging(report: Dict[str, Any], *, aggregate_customers: bool = False) -> Dict[str, Any]:
    """Convert QuickBooks report payload into collections-ready summary."""

    customers: Dict[str, Dict[str, Any]] = {}

    for txn in _extract_transactions(report):
        key = txn.customer_ref or _customer_key(txn.customer)
        record = customers.setdefault(
            key,
            {
                "customer": txn.customer,
                "external_ref": txn.customer_ref,
                "total_balance": Decimal("0"),
                "buckets": {bucket: Decimal("0") for bucket in BUCKET_ORDER},
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

        # prefer human-friendly casing from the first positive transaction
        if record["customer"] != txn.customer and record["positive_transactions"]:
            record["customer"] = record["positive_transactions"][0].customer
        if not record["external_ref"] and txn.customer_ref:
            record["external_ref"] = txn.customer_ref

    output: List[Dict[str, Any]] = []

    bucket_rank = {bucket: idx for idx, bucket in enumerate(BUCKET_ORDER)}

    records: List[Dict[str, Any]]
    if aggregate_customers:
        records = _aggregate_customer_records(customers.values())
    else:
        records = list(customers.values())

    for record in records:
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
        external_ref = record.get("external_ref")

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
            if oldest_txn.customer_ref:
                external_ref = oldest_txn.customer_ref

        bucket_output = {k: float(v) for k, v in record["buckets"].items()}
        if recommended_bucket:
            bucket_output = {key: 0.0 for key in BUCKET_ORDER}
            bucket_output[recommended_bucket] = float(total_balance)

        row = {
            "customer": record["customer"],
            "external_ref": external_ref,
            "total_balance": float(total_balance),
            "buckets": bucket_output,
            "credits": float(record["credits"]),
            "recommended_action": recommended_action,
            "oldest_invoice": oldest_invoice_info,
        }

        if aggregate_customers:
            refs = record.get("external_refs", [])
            if not refs and external_ref:
                refs = [external_ref]
            row["external_refs"] = refs

        output.append(row)

    # sort by largest total balance desc
    output.sort(key=lambda item: item["total_balance"], reverse=True)

    return {
        "generated_at": report.get("Header", {}).get("Time"),
        "rows": output,
    }


__all__ = ["simplify_ar_aging"]
