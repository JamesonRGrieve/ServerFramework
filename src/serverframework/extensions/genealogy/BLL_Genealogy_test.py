"""Structural tests for the genealogy extension's BLL surface."""

import os

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "genealogy_test")

from serverframework.extensions.genealogy.BLL_Genealogy import (
    ALL_MODELS,
    PersonManager,
    PersonModel,
    RelationshipManager,
    RelationshipModel,
)


class TestModelManagerWiring:
    PAIRS = [
        (PersonModel, PersonManager),
        (RelationshipModel, RelationshipManager),
    ]

    def test_each_model_has_its_manager(self):
        for model, manager in self.PAIRS:
            assert model.Manager is manager
            assert manager._model is model

    def test_all_models_listed(self):
        assert set(ALL_MODELS) == {model for model, _ in self.PAIRS}


class TestCreateUpdateSearchPresent:
    def test_nested_classes_exist(self):
        for model in ALL_MODELS:
            assert hasattr(model, "Create"), f"{model.__name__} missing Create"
            assert hasattr(model, "Update"), f"{model.__name__} missing Update"
            assert hasattr(model, "Search"), f"{model.__name__} missing Search"


class TestPersonShape:
    def test_living_person_has_no_death_date(self):
        p = PersonModel.Create(name="Bob", birth_date=None, death_date=None)
        assert p.death_date is None

    def test_full_person(self):
        p = PersonModel.Create(
            name="Alice",
            description="Wizard",
            gender="female",
        )
        assert p.name == "Alice"
        assert p.gender == "female"


class TestRelationshipShape:
    def test_genealogy_default_is_person_to_person(self):
        # Standalone genealogy edge: both endpoints are Persons.
        r = RelationshipModel.Create(
            person_id="alice",
            target_person_id="bob",
            kind="ancestry",
            discriminator="biological",
        )
        assert r.person_id == "alice"
        assert r.target_person_id == "bob"
        assert r.kind == "ancestry"
        assert r.discriminator == "biological"

    def test_validity_window(self):
        from datetime import datetime

        r = RelationshipModel.Create(
            person_id="alice",
            target_person_id="bob",
            kind="partnership",
            discriminator="marriage",
            valid_from=datetime(2020, 6, 1),
            valid_to=datetime(2024, 12, 31),
        )
        assert r.valid_from.year == 2020
        assert r.valid_to.year == 2024

    def test_intensity_is_signed(self):
        r = RelationshipModel.Create(
            person_id="alice",
            target_person_id="bob",
            kind="social",
            discriminator="rival",
            intensity=-0.8,
        )
        assert r.intensity == -0.8

    def test_qualifier_is_distinct_from_discriminator(self):
        r = RelationshipModel.Create(
            person_id="alice",
            target_person_id="bob",
            kind="social",
            discriminator="ally",
            qualifier="since the war",
        )
        assert r.discriminator == "ally"
        assert r.qualifier == "since the war"
