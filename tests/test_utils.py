"""Tests for utils.py — slugify and entity_key."""

from custom_components.comap.utils import entity_key, slugify


def test_d_plus() -> None:
    assert slugify("D+") == "d_plus"


def test_delta_symbol() -> None:
    assert slugify("Generator <> Frequency") == "generator_delta_frequency"


def test_normal_name() -> None:
    assert slugify("Generator Voltage L1-N") == "generator_voltage_l1_n"


def test_leading_trailing_stripped() -> None:
    assert slugify("-RPM-") == "rpm"


def test_entity_key_skip_not_used() -> None:
    assert entity_key("Not Used") is None


def test_entity_key_skip_st() -> None:
    assert entity_key("ST") is None


def test_entity_key_skip_empty() -> None:
    assert entity_key("") is None


def test_entity_key_normal() -> None:
    assert entity_key("Engine Speed") == "engine_speed"


def test_entity_key_d_plus() -> None:
    assert entity_key("D+") == "d_plus"
