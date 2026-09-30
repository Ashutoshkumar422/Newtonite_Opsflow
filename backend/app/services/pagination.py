"""Keyset (cursor) pagination.

Offset pagination degrades linearly with page depth and skips/duplicates rows when items
change between page loads (items move as updated_at changes). Keyset pagination seeks directly
to `(sort_value, id) < (last_seen_value, last_seen_id)` using a matching composite index, so
every page costs the same regardless of depth.
"""

import base64
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, func, literal, tuple_

from app.core.errors import ValidationFailed
from app.models import WorkItem

FAR_FUTURE = date(9999, 12, 31)


@dataclass(frozen=True)
class SortSpec:
    columns: tuple[Any, ...]  # ORM attributes / SQL expressions
    kinds: tuple[str, ...]  # how to (de)serialise each cursor value: dt|int|date
    descending: bool


SORTS: dict[str, SortSpec] = {
    "-updated_at": SortSpec((WorkItem.updated_at, WorkItem.id), ("dt", "int"), True),
    "-created_at": SortSpec((WorkItem.created_at, WorkItem.id), ("dt", "int"), True),
    "-priority": SortSpec((WorkItem.priority, WorkItem.updated_at, WorkItem.id), ("int", "dt", "int"), True),
    "due_date": SortSpec(
        (func.coalesce(WorkItem.due_date, literal(FAR_FUTURE)), WorkItem.id), ("date", "int"), False
    ),
}


def _encode_value(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _decode_value(kind: str, v: Any) -> Any:
    if kind == "dt":
        return datetime.fromisoformat(v)
    if kind == "date":
        return date.fromisoformat(v)
    return int(v)


def encode_cursor(values: list[Any]) -> str:
    raw = json.dumps([_encode_value(v) for v in values], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, spec: SortSpec) -> list[Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded))
        if not isinstance(values, list) or len(values) != len(spec.kinds):
            raise ValueError
        return [_decode_value(k, v) for k, v in zip(spec.kinds, values, strict=True)]
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailed("Invalid pagination cursor", details={"param": "cursor"}) from exc


def apply_keyset(stmt: Select, spec: SortSpec, cursor: str | None, limit: int) -> Select:
    if cursor:
        values = decode_cursor(cursor, spec)
        row = tuple_(*spec.columns)
        stmt = stmt.where(row < tuple_(*values) if spec.descending else row > tuple_(*values))
    order = [c.desc() if spec.descending else c.asc() for c in spec.columns]
    return stmt.order_by(*order).limit(limit + 1)  # +1 to detect whether another page exists


def cursor_values(item: WorkItem, spec_name: str) -> list[Any]:
    if spec_name == "-updated_at":
        return [item.updated_at, item.id]
    if spec_name == "-created_at":
        return [item.created_at, item.id]
    if spec_name == "-priority":
        return [item.priority, item.updated_at, item.id]
    return [item.due_date or FAR_FUTURE, item.id]
