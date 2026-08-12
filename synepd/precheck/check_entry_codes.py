"""Check uniqueness of primary or primary-plus-alias entry codes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class EntryCodeUniquenessCheck:
    """Duplicate four-field entry codes and the records using them."""

    collisions: dict[str, tuple[int, ...]]
    include_aliases: bool

    @property
    def valid(self) -> bool:
        return not self.collisions


def check_entry_code_uniqueness(
    records: Iterable[dict[str, Any]], *, include_aliases: bool = False
) -> EntryCodeUniquenessCheck:
    """Return all entry codes assigned to more than one record.

    Primary-code uniqueness is the release invariant.  ``include_aliases`` is
    useful for the stronger catalog invariant in which an alias must also map
    to exactly one record.
    """
    users: dict[str, list[int]] = defaultdict(list)
    for record in records:
        record_id = record.get("id")
        if not isinstance(record_id, int) or isinstance(record_id, bool):
            raise ValueError("Every record must have an integer id")
        primary = record.get("entry_code")
        if not isinstance(primary, str) or not primary:
            raise ValueError(f"Record {record_id} has an invalid primary entry code")
        if include_aliases:
            aliases = record.get("entry_codes")
            if aliases is None:
                codes = [primary]
            else:
                if not isinstance(aliases, (list, tuple)):
                    raise ValueError(
                        f"Record {record_id} entry_codes must be a list"
                    )
                if aliases.count(primary) != 1:
                    raise ValueError(
                        f"Record {record_id} entry_codes must contain its "
                        "primary entry_code exactly once"
                    )
                codes = [primary, *aliases]
        else:
            codes = [primary]
        for code in codes:
            if not isinstance(code, str) or not code:
                raise ValueError(f"Record {record_id} has an invalid entry code")
        for code in dict.fromkeys(codes):
            users[code].append(record_id)

    collisions = {
        code: tuple(record_ids)
        for code, record_ids in sorted(users.items())
        if len(record_ids) > 1
    }
    return EntryCodeUniquenessCheck(
        collisions=collisions,
        include_aliases=include_aliases,
    )
