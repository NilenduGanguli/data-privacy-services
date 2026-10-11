from dps import __version__


def test_version_is_semver() -> None:
    assert __version__.count(".") == 2
