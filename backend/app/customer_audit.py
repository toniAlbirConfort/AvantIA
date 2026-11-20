"""Audit: customers who booked for someone else and never appear as the guest themselves.

The Avantio export keeps the customer who made the booking ("Cliente") apart from the guest
who stays ("Ocupante"). Usually they are the same person; sometimes a customer books for a
relative or a friend and never stays. Such a customer is in the directory and in the
reservation data (the customer email is stored with the booking), so the check of email
consultations not registered in Avantio (``app.registration``) can take them for a guest
or for an already registered request. This report makes those customers visible.

Rules, read-only and recomputed from the latest valid reservation import:

* A reservation is "for someone else" when none of the customer's surnames appears in the
  occupant's name and their emails do not match. Names are compared with the same
  spelling-tolerant form as the registration check (Sørensen = SOERENSEN = Sorensen).
* Only direct-channel reservations count: on portals and agencies the "customer" is the
  intermediary itself.
* Companies, clubs and agencies acting as customers are listed apart: booking for their
  people is their normal business.
* A customer "never stays" when, in all their reservations (any channel or status), they
  never appear as the occupant.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics import channel_of
from app.models import Customer, RegistrationCheck
from app.planning import consolidated_reservations, details_for, latest_source_batch
from app.registration import skeleton, words

IGNORED_STATUSES = frozenset({"owner", "unavailable"})
# Legal forms and words that say the customer is an organisation, not a person.
COMPANY = re.compile(
    r"\b(s\.?\s?l\.?\s?u?|s\.?\s?a\.?\s?u?|c\.?\s?b|s\.?\s?r\.?\s?l|s\.?\s?n\.?\s?c|lda|ldt|ltd|"
    r"limited|gmbh|b\.?\s?v|n\.?\s?v|ehf|a/?s|aps|ab|oy|inc|llc|corp|unipessoal|sociedad|"
    r"club|asociaci[oó]n|fundaci[oó]n|hotel|viajes|travel|tours?|realty|homes|properties|"
    r"inmobiliaria|invest|agencia|agency|events?|transport|ayuntamiento|ajuntament|kommune|"
    r"gemeente)\b\.?",
    re.IGNORECASE,
)


def is_company(name: str | None) -> bool:
    return bool(COMPANY.search(name or ""))


def _tokens(value: str | None) -> set[str]:
    return {skeleton(token) for token in words(value)}


def customer_surnames(customer: Customer) -> set[str]:
    """Surnames of a directory customer; the whole name when the surname fields are empty."""
    found = _tokens(" ".join(filter(None, (customer.last_name, customer.second_last_name))))
    if found:
        return found
    tokens = [skeleton(token) for token in words(customer.display_name)]
    return set(tokens[1:]) if len(tokens) >= 2 else set(tokens)


def _same_person(surnames: set[str], occupant: str, customer_email: str, occupant_email: str) -> bool:
    if customer_email and occupant_email and customer_email == occupant_email:
        return True
    if not surnames:
        return True  # nothing to compare with: never claim it was someone else
    return bool(surnames & _tokens(occupant))


def _email(value: str | None) -> str:
    return (value or "").strip().lower()


def booked_for_others_view(db: Session) -> dict:
    empty = {
        "has_snapshot": False, "snapshot_at": None, "generated_at": datetime.now().astimezone(),
        "summary": {
            "reservations": 0, "with_customer": 0, "for_others": 0, "for_others_direct": 0,
            "for_others_intermediaries": 0, "customers_never_stay": 0, "single_reservation": 0,
            "customers_also_stay": 0, "companies": 0, "consultations": 0,
        },
        "never_stay": [], "also_stay": [], "companies": [],
    }
    batch = latest_source_batch(db, "reservations")
    if batch is None:
        return empty

    rows = [
        row for row in consolidated_reservations(db, batch).values()
        if row.status not in IGNORED_STATUSES
    ]
    details = details_for(db, (row.locator for row in rows))
    ids = {detail.customer_avantio_id for detail in details.values() if detail.customer_avantio_id}
    customers = {
        customer.avantio_id: customer
        for customer in db.scalars(select(Customer).where(Customer.avantio_id.in_(ids)))
    } if ids else {}

    with_customer = 0
    for_others_intermediaries = 0
    stays_as_guest: set[str] = set()
    occupant_emails: set[str] = set()
    for_others: dict[str, list[dict]] = defaultdict(list)
    surnames_of = {key: customer_surnames(customer) for key, customer in customers.items()}

    for row in rows:
        detail = details.get(row.locator)
        if detail and detail.occupant_email:
            occupant_emails.add(_email(detail.occupant_email))
        customer = customers.get(detail.customer_avantio_id) if detail else None
        if customer is None:
            continue
        with_customer += 1
        if _same_person(
            surnames_of[customer.avantio_id], row.display_name,
            _email(detail.customer_email), _email(detail.occupant_email),
        ):
            stays_as_guest.add(customer.avantio_id)
            continue
        channel = channel_of(row.portal)
        if channel != "Directo":
            for_others_intermediaries += 1
            continue
        for_others[customer.avantio_id].append({
            "locator": row.locator,
            "status": row.status,
            "created_on": row.created_on,
            "start_date": row.start_date,
            "end_date": row.end_date,
            "nights": row.nights,
            "portal": row.portal,
            "occupant": row.display_name,
            "total": row.total,
        })

    never_stay, also_stay, companies = [], [], []
    for avantio_id, bookings in for_others.items():
        customer = customers[avantio_id]
        bookings.sort(key=lambda item: (item["start_date"], item["locator"]), reverse=True)
        item = {
            "avantio_id": avantio_id,
            "name": customer.display_name,
            "customer_class": customer.customer_class,
            "reservations": bookings,
            "count": len(bookings),
            "total": sum((booking["total"] for booking in bookings), Decimal(0)),
            "last_start": bookings[0]["start_date"],
            "_email": _email(customer.email),
        }
        if is_company(customer.display_name):
            companies.append(item)
        elif avantio_id in stays_as_guest or (item["_email"] and item["_email"] in occupant_emails):
            also_stay.append(item)
        else:
            never_stay.append(item)

    # Consultations from these customers seen by the registration check, and what it decided.
    emails = {item["_email"] for item in never_stay if item["_email"]}
    for item in never_stay:
        emails.update(
            _email(details[booking["locator"]].customer_email)
            for booking in item["reservations"]
            if details.get(booking["locator"]) and details[booking["locator"]].customer_email
        )
    checks = list(db.scalars(
        select(RegistrationCheck)
        .where(func.lower(RegistrationCheck.customer_email).in_(emails))
        .order_by(RegistrationCheck.received_at.desc())
    )) if emails else []
    checks_by_email: dict[str, list[dict]] = defaultdict(list)
    for check in checks:
        checks_by_email[_email(check.customer_email)].append({
            "id": check.id,
            "conversation_id": check.conversation_id,
            "received_at": check.received_at,
            "status": check.status,
            "reason": check.reason,
            "locator": check.locator,
        })
    for item in never_stay:
        own = {item["_email"]} | {
            _email(details[booking["locator"]].customer_email)
            for booking in item["reservations"]
            if details.get(booking["locator"])
        }
        item["consultations"] = [
            check for address in sorted(own - {""}) for check in checks_by_email.get(address, [])
        ]

    def order(items: list[dict]) -> list[dict]:
        items.sort(key=lambda item: (-item["last_start"].toordinal(), item["name"]))
        for item in items:
            item.pop("_email", None)
        return items

    never_stay, also_stay, companies = order(never_stay), order(also_stay), order(companies)
    direct = sum(item["count"] for item in never_stay + also_stay + companies)
    return {
        "has_snapshot": True,
        "snapshot_at": batch.created_at,
        "generated_at": empty["generated_at"],
        "summary": {
            "reservations": len(rows),
            "with_customer": with_customer,
            "for_others": direct + for_others_intermediaries,
            "for_others_direct": direct,
            "for_others_intermediaries": for_others_intermediaries,
            "customers_never_stay": len(never_stay),
            "single_reservation": sum(1 for item in never_stay if item["count"] == 1),
            "customers_also_stay": len(also_stay),
            "companies": len(companies),
            "consultations": sum(len(item["consultations"]) for item in never_stay),
        },
        "never_stay": never_stay,
        "also_stay": also_stay,
        "companies": companies,
    }
