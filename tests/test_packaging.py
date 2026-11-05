"""Dependency-policy guard for the published package.

Sugar is installed *into other tools' environments*: the Hermes plugin pulls
``sugarai`` into an environment where Hermes pins its own core dependencies
exactly. An exact ``==`` pin in our runtime deps therefore turns into an
unsatisfiable union for every user of the host tool.

That has now happened twice — ``click`` (fixed in #80) and
``python-dotenv``/``setuptools`` (NousResearch/hermes-agent#149282) — so the
policy is enforced here rather than rediscovered in a host tool's CI.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _project() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_runtime_dependencies_have_no_exact_pins():
    offenders = [dep for dep in _project()["dependencies"] if "==" in dep]
    assert not offenders, (
        "exact runtime pins collide with host tools' own pins; "
        f"use a floor plus an upper bound instead: {offenders}"
    )


def test_runtime_dependencies_declare_an_upper_bound():
    offenders = [dep for dep in _project()["dependencies"] if "<" not in dep]
    assert (
        not offenders
    ), f"add an upper bound so a future major cannot break users: {offenders}"


def test_setuptools_is_not_a_runtime_dependency():
    # setuptools belongs in [build-system]. As a runtime dep it pins every host
    # that ships a different one (Hermes resolves 83.0.0).
    declared = " ".join(_project()["dependencies"]).lower()
    assert "setuptools" not in declared


def test_python_dotenv_range_accepts_the_host_pin():
    # Hermes pins python-dotenv==1.2.4 on Python 3.14; our range must admit it.
    dep = next(
        dep for dep in _project()["dependencies"] if dep.startswith("python-dotenv")
    )
    assert dep.startswith("python-dotenv>=1.2.2,<2"), dep
