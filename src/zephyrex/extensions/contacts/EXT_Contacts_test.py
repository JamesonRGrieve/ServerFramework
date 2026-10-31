# SPDX-License-Identifier: AGPL-3.0-or-later
"""The contacts extension: field checks, each provider's request bodies and
answers, CardDAV's query and vCards, ids that stay one path segment, and
real calls refusing a bogus token."""

from datetime import date

import httpx
import pytest

from zephyrex.extensions.contacts.EXT_Contacts import (
    EXT_Contacts,
    contact_fields,
    contact_limit,
)
from zephyrex.extensions.contacts.PRV_CardDAV import (
    PRV_CardDAV_Contacts,
    addressbook_query,
    changed_vcard,
    new_vcard,
)
from zephyrex.extensions.contacts.PRV_Google import PRV_Google_Contacts, google_birthday
from zephyrex.extensions.contacts.PRV_Microsoft import (
    PRV_Microsoft_Contacts,
    graph_birthday,
    odata_text,
)
from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    InvalidInputExternalError,
    PermanentExternalError,
)
from zephyrex.lib.DAV import CARDDAV, multistatus_resources

BOGUS = "not-a-real-token"
BORN = date(1990, 4, 12)


def _online(url: str) -> bool:
    try:
        httpx.head(url, timeout=5)
        return True
    except httpx.HTTPError:
        return False


def reachable(url: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(not _online(url), reason=f"{url} is unreachable")


class TestFields:
    def test_fields_given_are_checked_and_kept(self):
        assert contact_fields(
            " Alex Chen ", ["alex@example.com"], ["+1 555 0100"], BORN, None, None
        ) == {
            "name": "Alex Chen",
            "emails": ["alex@example.com"],
            "phones": ["+1 555 0100"],
            "birthday": BORN,
        }

    @pytest.mark.parametrize(
        "name, emails, phones",
        [
            ("  ", None, None),
            ("Alex", ["not an address"], None),
            ("Alex", None, ["  "]),
        ],
    )
    def test_bad_fields_are_refused(self, name, emails, phones):
        with pytest.raises(InvalidInputExternalError):
            contact_fields(name, emails, phones, None, None, None)

    def test_limits(self):
        assert contact_limit(50) == 50
        for bad in (0, 501):
            with pytest.raises(InvalidInputExternalError):
                contact_limit(bad)

    async def test_ability_arguments_are_checked_first(self):
        with pytest.raises(InvalidInputExternalError):
            await EXT_Contacts.list_contacts("google_contacts", limit=0)
        with pytest.raises(InvalidInputExternalError, match="nothing to change"):
            await EXT_Contacts.update_contact("google_contacts", "c1")


class TestGoogle:
    def test_bodies_and_answers(self):
        body = PRV_Google_Contacts._body(
            {
                "name": "Alex Chen",
                "emails": ["alex@example.com"],
                "birthday": BORN,
                "notes": "Met at the con",
            }
        )
        assert body["names"] == [{"unstructuredName": "Alex Chen"}]
        assert body["birthdays"] == [{"date": {"year": 1990, "month": 4, "day": 12}}]
        assert body["biographies"] == [
            {"value": "Met at the con", "contentType": "TEXT_PLAIN"}
        ]
        contact = PRV_Google_Contacts._contact(
            {
                "resourceName": "people/c123",
                "names": [{"displayName": "Alex Chen"}],
                "emailAddresses": [{"value": "alex@example.com"}, {"type": "no value"}],
                "phoneNumbers": [{"value": "+1 555 0100"}],
                "birthdays": [{"date": {"month": 4, "day": 12}}],
                "organizations": [{"name": "Boden"}],
            }
        )
        assert (contact["id"], contact["name"], contact["emails"]) == (
            "c123",
            "Alex Chen",
            ["alex@example.com"],
        )
        assert contact["birthday"] == "--04-12"
        assert contact["organization"] == "Boden"
        assert contact["url"] == "https://contacts.google.com/person/c123"

    def test_birthdays(self):
        assert (
            google_birthday({"date": {"year": 1990, "month": 4, "day": 12}})
            == "1990-04-12"
        )
        assert google_birthday({"date": {"year": 1990}}) is None

    async def test_an_id_stays_one_path_segment(self, provider_instance):
        instance = provider_instance(PRV_Google_Contacts, api_key=BOGUS)
        with pytest.raises(InvalidInputExternalError):
            await PRV_Google_Contacts.delete_contact(instance, "../people/me")


class TestMicrosoft:
    def test_bodies_and_answers(self):
        body = PRV_Microsoft_Contacts._body(
            {"name": "Priya Nair", "phones": ["+1 555 0101"], "birthday": BORN}
        )
        assert body["displayName"] == "Priya Nair"
        assert body["homePhones"] == ["+1 555 0101"]
        assert body["birthday"] == "1990-04-12T00:00:00Z"
        contact = PRV_Microsoft_Contacts._contact(
            {
                "id": "AAMk",
                "displayName": "Priya Nair",
                "emailAddresses": [{"address": "priya@example.com", "name": "Priya"}],
                "mobilePhone": "+1 555 0102",
                "homePhones": ["+1 555 0101"],
                "birthday": "1990-04-12T00:00:00Z",
                "companyName": "Second Chance Silicon",
            }
        )
        assert contact["phones"] == ["+1 555 0102", "+1 555 0101"]
        assert contact["birthday"] == "1990-04-12"
        assert graph_birthday(None) is None

    def test_a_search_quotes_its_text(self):
        assert odata_text("O'Brien") == "'O''Brien'"


# An addressbook-query answer: a full vCard and one with a yearless birthday.
CARDDAV_MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
 <d:response><d:href>/book/morgan.vcf</d:href><d:propstat><d:prop>
  <card:address-data>BEGIN:VCARD
VERSION:3.0
UID:morgan
FN:Morgan Lee
N:Lee;Morgan;;;
EMAIL;TYPE=INTERNET:morgan@example.com
TEL;TYPE=CELL:+1 555 0103
BDAY:1992-11-03
ORG:Boden Research
NOTE:Comet partner of Theo
END:VCARD
</card:address-data></d:prop></d:propstat></d:response>
 <d:response><d:href>/book/alex%20chen.vcf</d:href><d:propstat><d:prop>
  <card:address-data>BEGIN:VCARD
VERSION:4.0
UID:alex
FN:Alex Chen
BDAY:--0412
END:VCARD
</card:address-data></d:prop></d:propstat></d:response>
 <d:response><d:href>/book/</d:href><d:propstat><d:prop/></d:propstat></d:response>
</d:multistatus>"""


class TestCardDAV:
    def test_the_query_matches_name_or_address_and_escapes_its_text(self):
        query = addressbook_query("Lee & co", 25)
        assert (
            '<C:prop-filter name="FN">' in query
            and '<C:prop-filter name="EMAIL">' in query
        )
        assert "Lee &amp; co" in query
        assert "<C:nresults>25</C:nresults>" in query
        assert "<C:filter" not in addressbook_query(None, 25)

    def test_an_answer_lists_each_contact_by_name(self):
        found = multistatus_resources(CARDDAV_MULTISTATUS, f"{CARDDAV}address-data")
        alex, morgan = PRV_CardDAV_Contacts._contacts(found)
        assert (alex["id"], alex["name"], alex["birthday"]) == (
            "alex chen.vcf",
            "Alex Chen",
            "--04-12",
        )
        assert morgan["emails"] == ["morgan@example.com"]
        assert morgan["phones"] == ["+1 555 0103"]
        assert (morgan["birthday"], morgan["organization"]) == (
            "1992-11-03",
            "Boden Research",
        )
        assert morgan["notes"] == "Comet partner of Theo"

    def test_a_new_contact_round_trips(self):
        body = new_vcard(
            "uid-1",
            {"name": "Riley Quinn", "emails": ["riley@example.com"], "birthday": BORN},
        )
        [contact] = PRV_CardDAV_Contacts._contacts([("uid-1.vcf", body.decode())])
        assert (contact["name"], contact["emails"], contact["birthday"]) == (
            "Riley Quinn",
            ["riley@example.com"],
            "1990-04-12",
        )

    def test_a_change_replaces_only_what_it_names(self):
        original = new_vcard(
            "uid-2",
            {"name": "Theo Park", "emails": ["old@example.com"], "phones": ["1"]},
        )
        changed = changed_vcard(
            original.decode(), {"emails": ["new@example.com"], "organization": "Boden"}
        )
        [contact] = PRV_CardDAV_Contacts._contacts([("uid-2.vcf", changed.decode())])
        assert contact["emails"] == ["new@example.com"]
        assert (contact["name"], contact["phones"], contact["organization"]) == (
            "Theo Park",
            ["1"],
            "Boden",
        )

    def test_a_resource_that_is_not_a_vcard_is_a_precise_error(self):
        with pytest.raises(PermanentExternalError, match="not a vCard"):
            changed_vcard("<html>Sign in</html>", {"name": "x"})

    async def test_an_id_stays_one_path_segment(self, provider_instance):
        instance = provider_instance(
            PRV_CardDAV_Contacts,
            api_key=BOGUS,
            settings={"addressbook_url": "https://carddav.example.com/book/"},
        )
        for bad in ("../other.vcf", "..", ""):
            with pytest.raises(InvalidInputExternalError):
                await PRV_CardDAV_Contacts.delete_contact(instance, bad)


class TestRefusedTokens:
    @reachable("https://people.googleapis.com")
    async def test_google(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_Google_Contacts.list_contacts(
                provider_instance(PRV_Google_Contacts, api_key=BOGUS), None, 5
            )

    @reachable("https://graph.microsoft.com")
    async def test_microsoft(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_Microsoft_Contacts.list_contacts(
                provider_instance(PRV_Microsoft_Contacts, api_key=BOGUS), None, 5
            )

    @reachable("https://contacts.icloud.com")
    async def test_carddav(self, provider_instance):
        with pytest.raises(AuthExternalError):
            await PRV_CardDAV_Contacts.list_contacts(
                provider_instance(
                    PRV_CardDAV_Contacts,
                    api_key=BOGUS,
                    settings={
                        "username": "nobody@example.com",
                        "addressbook_url": "https://contacts.icloud.com/",
                    },
                ),
                None,
                5,
            )
