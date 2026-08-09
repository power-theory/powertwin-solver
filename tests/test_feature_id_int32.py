"""Pin the urbanopt_feature_id int32 invariant.

BuildResidentialModel declares urbanopt_feature_id via makeIntegerArgument -- an OpenStudio Integer,
i.e. a 32-bit int. National building ids are collection_id * 10**10 + asset_id (~10**10-10**11),
which OVERFLOWS it: setValue returns false and OpenStudio reports the misleading "passed argument
has type 'String' while 'Integer' was expected", failing 100% of residential buildings before any
measure runs. PowerTwin.rb coerces with `% 2_000_000_000`.

The e2e leaf fixture uses ids ~800000, four orders of magnitude below production, so it can never
catch this class -- hence this test asserts the invariant directly at national magnitude.
"""

import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPER = os.path.join(REPO, "solver", "upload", "PowerTwin.rb")
INT32_MAX = 2**31 - 1
MODULUS = 2_000_000_000


def _building_id(collection_id, asset_id):
    """The id scheme used sampler -> sim -> collector (stratified_sampler / generateFeatureFile)."""
    return collection_id * 10**10 + asset_id


def test_mapper_applies_the_modulo():
    src = open(MAPPER).read()
    assert re.search(r"raw_id\s*=\s*args\[:urbanopt_feature_id\]\.to_i", src) and \
        re.search(r"recovered\s*=\s*raw_id\s*%\s*2_000_000_000", src), \
        "PowerTwin.rb must coerce urbanopt_feature_id into int32 range"
    # and the scheme guard must accompany it (silent collisions otherwise -- see test_solver_contracts)
    assert "(raw_id / 10**10) * 10**10 + recovered != raw_id" in src


@pytest.mark.parametrize("collection_id", [1, 2, 9, 44, 51])
@pytest.mark.parametrize("asset_id", [1, 1023, 2621725, 9_999_999])
def test_coerced_id_is_int32_safe(collection_id, asset_id):
    bid = _building_id(collection_id, asset_id)
    assert bid > INT32_MAX, "test is vacuous unless the raw id actually overflows"
    assert 0 <= bid % MODULUS <= INT32_MAX


@pytest.mark.parametrize("collection_id", [1, 7, 51])
def test_coercion_recovers_asset_id(collection_id):
    # 10**10 is an exact multiple of 2_000_000_000, so the modulo returns the asset_id itself --
    # this is what keeps the value meaningful in the measure's error strings.
    for asset_id in (1, 1023, 2621725):
        assert _building_id(collection_id, asset_id) % MODULUS == asset_id


def test_coercion_is_injective_within_a_collection():
    # ids must not collide after coercion, or two buildings share a feature id
    for collection_id in (1, 23, 51):
        ids = [_building_id(collection_id, a) for a in range(1, 5000)]
        assert len({i % MODULUS for i in ids}) == len(ids)


def test_asset_id_bound_is_documented():
    # the modulo only stays injective while asset_id < MODULUS; national collections are ~10**6-10**7
    # buildings, so this holds with 2+ orders of margin. Fails loudly if the scheme ever changes.
    assert 10**7 < MODULUS
