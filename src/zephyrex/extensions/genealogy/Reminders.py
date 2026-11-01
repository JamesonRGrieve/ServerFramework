# SPDX-License-Identifier: AGPL-3.0-or-later
"""Birthdays and anniversaries from a tree, sent as notifications.

Each account is reminded of the people in its own tree: their birthdays,
and the anniversaries of its own partnerships (from the tie's ``valid_from``).
A reminder goes a week ahead and on the day, through the auth_notifications
extension, once: its notification names the occasion and the lead, so a
second pass finds it and sends nothing.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Tuple

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger
from zephyrex.logic.AbstractService import AbstractService

# How many days ahead a reminder goes: a week before, and on the day.
LEAD_DAYS = (7, 0)
SERVICE_ID = "genealogy_reminders"
# The service looks every few hours; a reminder is sent once, so a busy day is harmless.
TICK_SECONDS = 6 * 60 * 60
REFERENCE_TYPE = "genealogy_reminder"
FEBRUARY, LEAP_DAY, LAST_FEBRUARY_DAY = 2, 29, 28

Kind = Literal["birthday", "anniversary"]


@dataclass(frozen=True)
class Occasion:
    """A birthday or anniversary ``days`` from today, falling on ``on``;
    ``years`` old (or together) then, when the first year is known."""

    kind: Kind
    person_id: str
    name: str
    on: date
    days: int
    years: Optional[int]


def _yearly(origin: date, year: int) -> date:
    """``origin``'s day in ``year``: 29 February is kept on the 28th in other years."""
    if origin.month == FEBRUARY and origin.day == LEAP_DAY:
        try:
            return date(year, FEBRUARY, LEAP_DAY)
        except ValueError:
            return date(year, FEBRUARY, LAST_FEBRUARY_DAY)
    return origin.replace(year=year)


def next_occurrence(origin: date, today: date) -> date:
    """The next time (today included) ``origin``'s day comes round."""
    this_year = _yearly(origin, today.year)
    return this_year if this_year >= today else _yearly(origin, today.year + 1)


def occasion(
    kind: Kind, person_id: str, name: str, origin: date, today: date
) -> Occasion:
    on = next_occurrence(origin, today)
    years = on.year - origin.year
    return Occasion(
        kind, person_id, name, on, (on - today).days, years if years > 0 else None
    )


def due(occasions: Iterable[Occasion]) -> List[Occasion]:
    """The occasions a reminder is due for today."""
    return [found for found in occasions if found.days in LEAD_DAYS]


def reference(found: Occasion) -> str:
    """What names a reminder, so it is sent once."""
    return f"{found.kind}:{found.person_id}:{found.on.isoformat()}:{found.days}"


def message(found: Occasion) -> Tuple[str, str]:
    """A reminder's title and text."""
    when = "today" if found.days == 0 else "in a week"
    day = f"{found.on.strftime('%A')} {found.on.day} {found.on.strftime('%B')}"
    if found.kind == "birthday":
        turning = f" They turn {found.years}." if found.years is not None else ""
        return f"{found.name}’s birthday is {when}", f"On {day}.{turning}"
    together = (
        f" {found.years} year{'s' if found.years != 1 else ''} together."
        if found.years is not None
        else ""
    )
    return f"Your anniversary with {found.name} is {when}", f"On {day}.{together}"


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _rows(
    model: Any, model_registry: Any, conditions: Callable[[Any], list]
) -> List[Any]:
    """``model``'s live rows matching ``conditions(db)``, read as ROOT."""
    db = model.DB(model_registry.DB.manager.Base)
    return (
        db.list(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            filters=[*conditions(db), db.deleted_at.is_(None)],
            return_type="dto",
            override_dto=model,
        )
        or []
    )


def occasions_for_all(model_registry: Any, today: date) -> Dict[str, List[Occasion]]:
    """Every account's occasions, by account: the birthdays in its tree (not
    its own), and the anniversaries of its own current partnerships."""
    from zephyrex.extensions.genealogy.BLL_Genealogy import (
        PersonModel,
        RelationshipModel,
    )
    from zephyrex.extensions.genealogy.Relationships import PARTNERSHIP

    people = _rows(PersonModel, model_registry, lambda db: [db.birth_date.isnot(None)])
    found: Dict[str, List[Occasion]] = {}
    names: Dict[str, str] = {}
    for person in _rows(PersonModel, model_registry, lambda db: []):
        names[person.id] = person.name or "Someone"
    for person in people:
        owner = person.created_by_user_id
        born = _as_date(person.birth_date)
        if owner is None or born is None or person.linked_user_id == owner:
            continue
        found.setdefault(owner, []).append(
            occasion("birthday", person.id, names[person.id], born, today)
        )
    selves = {
        person.id
        for person in _rows(
            PersonModel, model_registry, lambda db: [db.linked_user_id.isnot(None)]
        )
        if person.linked_user_id == person.created_by_user_id
    }
    ties = _rows(
        RelationshipModel,
        model_registry,
        lambda db: [
            db.kind == PARTNERSHIP,
            db.valid_from.isnot(None),
            db.valid_to.is_(None),
        ],
    )
    for tie in ties:
        began = _as_date(tie.valid_from)
        owner = tie.created_by_user_id
        if owner is None or began is None or tie.person_id not in selves:
            continue
        partner = tie.target_person_id
        found.setdefault(owner, []).append(
            occasion(
                "anniversary", partner, names.get(partner, "your partner"), began, today
            )
        )
    return found


def send_reminders(model_registry: Any, today: date) -> int:
    """Send today's due reminders that have not been sent; answers how many went."""
    from zephyrex.extensions.auth_notifications.BLL_Auth_Notifications import (
        NotificationModel,
        UserNotificationModel,
    )

    root = env("ROOT_ID")
    base = model_registry.DB.manager.Base
    notifications = NotificationModel.DB(base)
    deliveries = UserNotificationModel.DB(base)
    sent = 0
    for owner, occasions in occasions_for_all(model_registry, today).items():
        for found in due(occasions):
            key = reference(found)
            already = notifications.list(
                requester_id=root,
                model_registry=model_registry,
                filters=[
                    notifications.user_id == owner,
                    notifications.reference_type == REFERENCE_TYPE,
                    notifications.reference_id == key,
                ],
            )
            if already:
                continue
            title, content = message(found)
            made = notifications.create(
                requester_id=root,
                model_registry=model_registry,
                return_type="dto",
                override_dto=NotificationModel,
                user_id=owner,
                title=title,
                content=content,
                reference_type=REFERENCE_TYPE,
                reference_id=key,
            )
            deliveries.create(
                requester_id=root,
                model_registry=model_registry,
                user_id=owner,
                notification_id=made.id,
            )
            sent += 1
    return sent


class ReminderService(AbstractService):
    """Sends the day's birthday and anniversary reminders, as ROOT."""

    def __init__(
        self,
        requester_id: str,
        model_registry: Any,
        interval_seconds: int = TICK_SECONDS,
        **kwargs: Any,
    ):
        super().__init__(
            requester_id=requester_id,
            interval_seconds=interval_seconds,
            service_id=SERVICE_ID,
            **kwargs,
        )
        self.model_registry = model_registry

    async def update(self) -> None:
        today = datetime.now(timezone.utc).date()
        try:
            sent = await asyncio.to_thread(send_reminders, self.model_registry, today)
        except Exception as exc:  # the database is away: try again next tick
            logger.warning(
                "genealogy: reminders failed (%s: %s)", type(exc).__name__, exc
            )
            return
        if sent:
            logger.info("genealogy: sent %s reminder(s)", sent)
