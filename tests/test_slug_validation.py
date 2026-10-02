import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from spec_package_support import is_development_record_slug, validate_slug


@pytest.mark.parametrize(
    "slug",
    ["test-slug", "2026-05-01_test-slug", "abc", "a-b_c", "foo_bar-baz"],
)
def test_valid_slugs(slug):
    assert validate_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    ["-bad", "bad-", "_bad", "bad_", "has--double", "has__double", "UPPER", "a b", ""],
)
def test_invalid_slugs(slug):
    with pytest.raises(ValueError):
        validate_slug(slug)


@pytest.mark.parametrize(
    "slug",
    ["2026-05-01_a", "2026-05-01_test-slug", "2026-12-31_spec_architecture_standard"],
)
def test_development_record_slugs(slug):
    assert is_development_record_slug(slug) is True


@pytest.mark.parametrize(
    "slug",
    [
        "test-slug",
        "2026-5-01_test",
        "2026-05-01",
        "2026-05-01_",
        "2026-05-01_Test",
        "2026-99-99_test",
        "2026-05-01_has__double",
        "2026-05-01_has--double",
    ],
)
def test_non_development_record_slugs(slug):
    assert is_development_record_slug(slug) is False
