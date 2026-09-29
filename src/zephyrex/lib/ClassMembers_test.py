# SPDX-License-Identifier: AGPL-3.0-or-later
from zephyrex.lib.ClassMembers import (
    decorated_functions,
    instance_methods,
    static_or_plain_functions,
)


def _mark(fn):
    fn._marked = True
    return fn


class _Exploding:
    def __get__(self, instance, owner):
        raise AssertionError("a scan must not evaluate descriptors")


class Base:
    boom = _Exploding()

    @_mark
    def inherited(self):
        pass


class Sample(Base):
    @staticmethod
    @_mark
    def static_marked():
        pass

    @staticmethod
    def static_plain():
        pass

    @classmethod
    @_mark
    def class_marked(cls):
        pass

    def instance(self):
        pass


def test_scans_never_evaluate_descriptors():
    static_or_plain_functions(Sample)
    decorated_functions(Sample, "_marked")
    instance_methods(Sample)


def test_static_or_plain_functions_unwraps_staticmethods():
    names = {name for name, _ in static_or_plain_functions(Sample)}
    assert {"inherited", "static_marked", "static_plain", "instance"} <= names
    assert "class_marked" not in names


def test_decorated_functions_filters_by_marker_and_returns_its_value():
    found = decorated_functions(Sample, "_marked")
    assert {name for name, _, _ in found} == {"inherited", "static_marked"}
    assert all(value is True for _, _, value in found)


def test_instance_methods_excludes_static_and_class_methods():
    names = {name for name, _ in instance_methods(Sample)}
    assert {"inherited", "instance"} <= names
    assert not names & {"static_marked", "static_plain", "class_marked"}
