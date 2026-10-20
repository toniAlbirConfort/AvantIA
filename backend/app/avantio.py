"""Boundary for the future Avantio integration.

The CSV provider is active today. Connecting Avantio requires a documented API
contract and credentials; this module keeps that change out of conversations.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from app.availability import AvailabilityResult, check_availability
from app.config import get_settings


class AvailabilityProvider(Protocol):
    def check(
        self,
        db: Session,
        *,
        property_query: str | None,
        arrival: str | None,
        departure: str | None,
        guests: int | None,
    ) -> AvailabilityResult: ...


class ReservationProvider(Protocol):
    def sync_reservations(self, db: Session) -> int: ...


class CsvAvailabilityProvider:
    def check(
        self,
        db: Session,
        *,
        property_query: str | None,
        arrival: str | None,
        departure: str | None,
        guests: int | None,
    ) -> AvailabilityResult:
        return check_availability(
            db, property_query=property_query, arrival=arrival, departure=departure, guests=guests
        )


class AvantioProvider:
    def check(
        self,
        db: Session,
        *,
        property_query: str | None,
        arrival: str | None,
        departure: str | None,
        guests: int | None,
    ) -> AvailabilityResult:
        raise NotImplementedError("Avantio API credentials and contract are not available")

    def sync_reservations(self, db: Session) -> int:
        raise NotImplementedError("Avantio API credentials and contract are not available")


def availability_provider() -> AvailabilityProvider:
    if get_settings().availability_source == "csv":
        return CsvAvailabilityProvider()
    if get_settings().availability_source == "avantio":
        return AvantioProvider()
    raise ValueError("AVAILABILITY_SOURCE must be csv or avantio")
