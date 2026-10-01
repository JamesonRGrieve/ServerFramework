# SPDX-License-Identifier: AGPL-3.0-or-later
"""GEDCOM reading (5.5, 5.5.1, 5.5.5 and 7.0) and writing (5.5.1).

A GEDCOM file is lines of ``level [@xref@] TAG [value]``. Individuals
(``INDI``) carry a name, sex and birth and death events; families (``FAM``)
join up to two partners (``HUSB``, ``WIFE``) and their children (``CHIL``).
A child's ``FAMC`` link may say how it belongs to the family (``PEDI``:
birth, adopted, foster).

Only exact dates (``12 MAR 1900``) become dates. A GEDCOM date may be
partial or qualified (``ABT 1900``, ``BET 1900 AND 1910``, ``MAR 1900``),
which a date cannot hold without inventing precision, so those are kept
verbatim in the person's notes instead.

Reading needs text in UTF-8 or ASCII; ANSEL, which only 5.5.1 permits, is
refused rather than misread.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

WRITTEN_VERSION = "5.5.1"
READABLE_VERSIONS = re.compile(r"^(5\.5(\.1|\.5)?|7\.0(\.\d+)?)$")

_LINE = re.compile(r"^\s*(\d+)\s+(?:(@[^@]+@)\s+)?(\S+)(?:\s(.*))?$")
_MONTHS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)
_EXACT_DATE = re.compile(r"^(\d{1,2})\s+([A-Z]{3})\s+(\d{3,4})$")
# PEDI values (5.5.1 lower case, 7.0 upper case) to parent roles.
_PEDIGREE_ROLE = {"birth": "biological", "adopted": "adopted", "foster": "foster"}
_SEX_TO_GENDER = {"M": "male", "F": "female", "X": "other"}
_GENDER_TO_SEX = {"male": "M", "female": "F"}


class GEDCOMError(ValueError):
    """The text is not GEDCOM this module can read."""


@dataclass
class _Node:
    level: int
    xref: Optional[str]
    tag: str
    value: str
    children: List["_Node"] = field(default_factory=list)

    def first(self, tag: str) -> Optional["_Node"]:
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, tag: str) -> List["_Node"]:
        return [c for c in self.children if c.tag == tag]


@dataclass
class GEDCOMPerson:
    xref: str
    name: str
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    death_date: Optional[date] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class GEDCOMFamily:
    xref: str
    partners: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)
    married: bool = False


@dataclass
class GEDCOMFile:
    version: str
    people: Dict[str, GEDCOMPerson]
    families: List[GEDCOMFamily]
    # (child xref, family xref) -> parent role, from the child's FAMC/PEDI.
    pedigree: Dict[Tuple[str, str], str]

    def child_role(self, child: str, family: str) -> str:
        """The role of ``family``'s partners as ``child``'s parents; GEDCOM
        reads a child with no ``PEDI`` as born to the family."""
        return self.pedigree.get((child, family), "biological")


def _parse_lines(text: str) -> List[_Node]:
    roots: List[_Node] = []
    stack: List[_Node] = []
    for number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        if not raw.strip():
            continue
        match = _LINE.match(raw)
        if not match:
            raise GEDCOMError(f"line {number} is not a GEDCOM line")
        level = int(match.group(1))
        node = _Node(
            level, match.group(2), match.group(3).upper(), match.group(4) or ""
        )
        while stack and stack[-1].level >= level:
            stack.pop()
        if level == 0:
            roots.append(node)
        elif not stack or stack[-1].level != level - 1:
            raise GEDCOMError(f"line {number} skips a level")
        else:
            if node.tag in ("CONT", "CONC"):
                parent = stack[-1]
                parent.value += ("\n" if node.tag == "CONT" else "") + node.value
                continue
            stack[-1].children.append(node)
        stack.append(node)
    return roots


def _version(roots: Sequence[_Node]) -> str:
    head = next((r for r in roots if r.tag == "HEAD"), None)
    gedc = head.first("GEDC") if head else None
    vers = gedc.first("VERS") if gedc else None
    if vers is None:
        raise GEDCOMError("no HEAD.GEDC.VERS: not a GEDCOM file")
    version = vers.value.strip()
    if not READABLE_VERSIONS.match(version):
        raise GEDCOMError(
            f"GEDCOM {version} is not supported (5.5, 5.5.1, 5.5.5 or 7.0)"
        )
    char = head.first("CHAR") if head else None
    if char is not None and char.value.strip().upper() == "ANSEL":
        raise GEDCOMError("ANSEL-encoded GEDCOM is not supported; export it as UTF-8")
    return version


def parse_date(value: str) -> Optional[date]:
    """An exact GEDCOM date (``12 MAR 1900``) as a date; None for anything
    partial, qualified or out of range."""
    match = _EXACT_DATE.match(value.strip().upper())
    if not match or match.group(2) not in _MONTHS:
        return None
    try:
        return date(
            int(match.group(3)), _MONTHS.index(match.group(2)) + 1, int(match.group(1))
        )
    except ValueError:
        return None


def format_date(value: date) -> str:
    return f"{value.day} {_MONTHS[value.month - 1]} {value.year}"


def _event(person: GEDCOMPerson, node: _Node, label: str) -> Optional[date]:
    when = node.first("DATE")
    if when is None or not when.value.strip():
        return None
    exact = parse_date(when.value)
    if exact is None:
        person.notes.append(f"{label}: {when.value.strip()}")
    return exact


def parse(text: str) -> GEDCOMFile:
    """Read GEDCOM 5.5, 5.5.1, 5.5.5 or 7.0."""
    roots = _parse_lines(text)
    version = _version(roots)
    people: Dict[str, GEDCOMPerson] = {}
    families: List[GEDCOMFamily] = []
    pedigree: Dict[Tuple[str, str], str] = {}
    for root in roots:
        if root.tag == "INDI" and root.xref:
            name_node = root.first("NAME")
            name = " ".join(
                (name_node.value if name_node else "").replace("/", " ").split()
            )
            person = GEDCOMPerson(xref=root.xref, name=name or "Unknown")
            sex = root.first("SEX")
            if sex is not None:
                person.gender = _SEX_TO_GENDER.get(sex.value.strip().upper())
            birth, death = root.first("BIRT"), root.first("DEAT")
            if birth is not None:
                person.birth_date = _event(person, birth, "Born")
            if death is not None:
                person.death_date = _event(person, death, "Died")
            person.notes.extend(
                n.value
                for n in root.all("NOTE")
                if n.value and not n.value.startswith("@")
            )
            for link in root.all("FAMC"):
                pedi = link.first("PEDI")
                role = _PEDIGREE_ROLE.get(pedi.value.strip().lower()) if pedi else None
                if role:
                    pedigree[(root.xref, link.value.strip())] = role
            people[root.xref] = person
        elif root.tag == "FAM" and root.xref:
            family = GEDCOMFamily(
                xref=root.xref, married=root.first("MARR") is not None
            )
            family.partners = [
                n.value.strip()
                for n in root.children
                if n.tag in ("HUSB", "WIFE") and n.value.strip() != "@VOID@"
            ]
            family.children = [
                n.value.strip() for n in root.all("CHIL") if n.value.strip() != "@VOID@"
            ]
            families.append(family)
    for family in families:
        for member in family.partners + family.children:
            if member not in people:
                raise GEDCOMError(
                    f"family {family.xref} names {member}, who has no INDI record"
                )
    return GEDCOMFile(
        version=version, people=people, families=families, pedigree=pedigree
    )


@dataclass(frozen=True)
class ExportPerson:
    id: str
    name: str
    gender: Optional[str] = None
    birth_date: Optional[date] = None
    death_date: Optional[date] = None


@dataclass(frozen=True)
class ExportFamily:
    """Up to two partners and the children they parent in one role."""

    partners: Tuple[str, ...]
    children: Tuple[str, ...] = ()
    married: bool = False
    role: str = "biological"


def _xref(index: int, prefix: str) -> str:
    return f"@{prefix}{index}@"


def _value(text: str) -> str:
    """A line value: one line, as GEDCOM lines cannot hold line breaks."""
    return " ".join(text.split())


def dumps(
    people: Iterable[ExportPerson],
    families: Iterable[ExportFamily],
    source: str = "zephyrex",
) -> str:
    """Write GEDCOM 5.5.1 (UTF-8)."""
    people = list(people)
    families = list(families)
    person_xref = {p.id: _xref(i, "I") for i, p in enumerate(people, start=1)}
    family_xref = [_xref(i, "F") for i, _ in enumerate(families, start=1)]
    lines = [
        "0 HEAD",
        f"1 SOUR {_value(source)}",
        "1 GEDC",
        f"2 VERS {WRITTEN_VERSION}",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
    ]
    links: Dict[str, List[str]] = {p.id: [] for p in people}
    for family, xref in zip(families, family_xref):
        for partner in family.partners:
            links[partner].append(f"1 FAMS {xref}")
        for child in family.children:
            links[child].append(f"1 FAMC {xref}")
            pedi = {
                "biological": "birth",
                "adopted": "adopted",
                "foster": "foster",
            }.get(family.role)
            if pedi:
                links[child].append(f"2 PEDI {pedi}")
    for person in people:
        lines.append(f"0 {person_xref[person.id]} INDI")
        lines.append(f"1 NAME {_value(person.name)}")
        lines.append(f"1 SEX {_GENDER_TO_SEX.get((person.gender or '').lower(), 'U')}")
        for tag, when in (("BIRT", person.birth_date), ("DEAT", person.death_date)):
            if when is not None:
                lines += [f"1 {tag}", f"2 DATE {format_date(when)}"]
        lines += links[person.id]
    genders = {p.id: (p.gender or "").lower() for p in people}
    for family, xref in zip(families, family_xref):
        lines.append(f"0 {xref} FAM")
        partners = sorted(family.partners, key=lambda p: genders.get(p) != "male")
        for tag, partner in zip(("HUSB", "WIFE"), partners):
            lines.append(f"1 {tag} {person_xref[partner]}")
        lines += [f"1 CHIL {person_xref[c]}" for c in family.children]
        if family.married:
            lines.append("1 MARR Y")
    lines.append("0 TRLR")
    return "\n".join(lines) + "\n"
