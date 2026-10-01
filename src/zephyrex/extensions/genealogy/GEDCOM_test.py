# SPDX-License-Identifier: AGPL-3.0-or-later
"""GEDCOM reading (5.5.1 and 7.0) and writing (5.5.1)."""

from datetime import date

import pytest

from zephyrex.extensions.genealogy.GEDCOM import (
    ExportFamily,
    ExportPerson,
    GEDCOMError,
    dumps,
    parse,
    parse_date,
)

GEDCOM_551 = """0 HEAD
1 SOUR Gramps
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Ada /Lovelace/
1 SEX F
1 BIRT
2 DATE 10 DEC 1815
1 DEAT
2 DATE 27 NOV 1852
1 FAMS @F1@
0 @I2@ INDI
1 NAME William /King/
1 SEX M
1 BIRT
2 DATE ABT 1805
1 FAMS @F1@
0 @I3@ INDI
1 NAME Byron /King/
1 SEX M
1 FAMC @F1@
2 PEDI birth
0 @I4@ INDI
1 NAME Foster /Child/
1 FAMC @F1@
2 PEDI foster
1 NOTE A note that
2 CONC  continues
2 CONT on a new line
0 @F1@ FAM
1 HUSB @I2@
1 WIFE @I1@
1 CHIL @I3@
1 CHIL @I4@
1 MARR
2 DATE 8 JUL 1835
0 TRLR
"""

GEDCOM_70 = """0 HEAD
1 GEDC
2 VERS 7.0
0 @I1@ INDI
1 NAME Sam /Smith/
1 SEX X
0 @I2@ INDI
1 NAME Kim /Smith/
1 FAMC @F1@
2 PEDI ADOPTED
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @VOID@
1 CHIL @I2@
0 TRLR
"""


class TestRead551:
    def test_people(self):
        file = parse(GEDCOM_551)
        assert file.version == "5.5.1"
        ada = file.people["@I1@"]
        assert ada.name == "Ada Lovelace"
        assert ada.gender == "female"
        assert ada.birth_date == date(1815, 12, 10)
        assert ada.death_date == date(1852, 11, 27)

    def test_an_approximate_date_is_kept_as_a_note_not_a_date(self):
        william = parse(GEDCOM_551).people["@I2@"]
        assert william.birth_date is None
        assert william.notes == ["Born: ABT 1805"]

    def test_continued_notes(self):
        foster = parse(GEDCOM_551).people["@I4@"]
        assert foster.notes == ["A note that continues\non a new line"]

    def test_family_and_pedigree(self):
        file = parse(GEDCOM_551)
        (family,) = file.families
        assert family.partners == ["@I2@", "@I1@"]
        assert family.children == ["@I3@", "@I4@"]
        assert family.married
        assert file.child_role("@I3@", "@F1@") == "biological"
        assert file.child_role("@I4@", "@F1@") == "foster"


class TestRead70:
    def test_version_sex_void_and_pedigree(self):
        file = parse(GEDCOM_70)
        assert file.version == "7.0"
        assert file.people["@I1@"].gender == "other"
        (family,) = file.families
        assert family.partners == ["@I1@"]
        assert file.child_role("@I2@", "@F1@") == "adopted"
        assert not family.married


class TestRefusals:
    def test_not_gedcom(self):
        with pytest.raises(GEDCOMError, match="not a GEDCOM"):
            parse("hello\nworld\n")

    def test_unsupported_version(self):
        with pytest.raises(GEDCOMError, match="4.0 is not supported"):
            parse("0 HEAD\n1 GEDC\n2 VERS 4.0\n0 TRLR\n")

    def test_ansel(self):
        with pytest.raises(GEDCOMError, match="ANSEL"):
            parse("0 HEAD\n1 GEDC\n2 VERS 5.5.1\n1 CHAR ANSEL\n0 TRLR\n")

    def test_a_skipped_level(self):
        with pytest.raises(GEDCOMError, match="skips a level"):
            parse("0 HEAD\n2 GEDC\n0 TRLR\n")

    def test_a_family_naming_a_missing_person(self):
        text = "0 HEAD\n1 GEDC\n2 VERS 5.5.1\n0 @F1@ FAM\n1 CHIL @I9@\n0 TRLR\n"
        with pytest.raises(GEDCOMError, match="@I9@"):
            parse(text)


class TestDates:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("10 DEC 1815", date(1815, 12, 10)),
            ("1 jan 1900", date(1900, 1, 1)),
            ("DEC 1815", None),
            ("1815", None),
            ("ABT 1815", None),
            ("31 FEB 1900", None),
        ],
    )
    def test_only_exact_dates(self, text, expected):
        assert parse_date(text) == expected


class TestWrite:
    def test_round_trip(self):
        people = [
            ExportPerson(
                "p1", "Ada Lovelace", "female", date(1815, 12, 10), date(1852, 11, 27)
            ),
            ExportPerson("p2", "William King", "male"),
            ExportPerson("p3", "Byron King", "male"),
            ExportPerson("p4", "Foster Child"),
        ]
        families = [
            ExportFamily(partners=("p1", "p2"), children=("p3",), married=True),
            ExportFamily(partners=("p1", "p2"), children=("p4",), role="foster"),
        ]
        text = dumps(people, families)
        assert "2 VERS 5.5.1" in text and "1 CHAR UTF-8" in text

        file = parse(text)
        assert file.version == "5.5.1"
        by_name = {p.name: p for p in file.people.values()}
        assert by_name["Ada Lovelace"].birth_date == date(1815, 12, 10)
        assert by_name["Ada Lovelace"].gender == "female"
        assert by_name["Foster Child"].gender is None
        first, second = file.families
        husband = next(x for x, p in file.people.items() if p.name == "William King")
        assert first.partners[0] == husband  # the man is HUSB
        assert first.married and not second.married
        foster = next(x for x, p in file.people.items() if p.name == "Foster Child")
        assert file.child_role(foster, second.xref) == "foster"

    def test_a_value_cannot_break_a_line(self):
        text = dumps([ExportPerson("p1", "Line\n0 @X@ INDI")], [])
        assert "1 NAME Line 0 @X@ INDI" in text
        assert len(parse(text).people) == 1
