from conftest import get_field_test_candidates
from serverframework.logic.BLL_Extensions import AbilityModel


def test_relationship_fields_excluded_from_field_candidates():
    candidates = get_field_test_candidates(AbilityModel)

    assert "extension" not in candidates
    assert "extension_id" in candidates
    assert "name" in candidates
