"""Cross-checking PPK answers from several bases (posproc/multibase.py), on made-up positions."""
import numpy as np

from posproc import multibase as mb
from posproc.ppk import POS_DTYPE

LAT0, LON0, H0 = 37.5, -77.5, 50.0
M_PER_DEG_LAT = np.radians(1) * mb.EARTH_R_M
N_EPOCHS = 100


def _pos(q, north_m=0.0, up_m=0.0, n=N_EPOCHS):
    p = np.zeros(n, POS_DTYPE)
    p["week"], p["t"] = 2400, 1000.0 + np.arange(n)
    p["lat"], p["lon"], p["h"] = LAT0 + north_m / M_PER_DEG_LAT, LON0, H0 + up_m
    p["q"] = q
    p["sdn"] = p["sde"] = p["sdu"] = 0.01
    return p


def _sol(site, km, q, north_m=0.0, up_m=0.0):
    return mb.BaseSolution(site, km, _pos(q, north_m, up_m))


def test_two_bases_agree_on_fixes():
    cc = mb.cross_check([_sol("FAR", 60, 1, 0.02), _sol("NEAR", 30, 1)])
    assert cc.primary == "NEAR"
    assert (cc.pos["q"] == 1).all() and len(cc.pos) == N_EPOCHS
    assert cc.per_base["NEAR"][mb.CONFIRMED] == N_EPOCHS
    assert abs(cc.accuracy["horizontal"] - 0.02) < 1e-3
    assert not cc.warnings


def test_two_fixes_that_disagree_both_thrown_out_floats_used():
    # the 2026-11-15 case: two bases fix 1 m apart; the third only floats, ~0.5 m from both
    cc = mb.cross_check([_sol("A", 39, 1, 1.0), _sol("B", 51, 1), _sol("C", 67, 2, 0.5)])
    assert (cc.pos["q"] == 2).all()
    assert cc.per_base["A"][mb.REJECTED] == N_EPOCHS and cc.per_base["B"][mb.REJECTED] == N_EPOCHS
    assert any("do not agree on a fixed answer" in w for w in cc.warnings)


def test_float_backed_fix_wins_over_lone_fix():
    cc = mb.cross_check([_sol("A", 39, 1, 1.0, 0.9), _sol("B", 51, 1), _sol("C", 67, 2, 0.2)])
    assert cc.per_base["A"][mb.REJECTED] == N_EPOCHS
    assert cc.per_base["B"][mb.FLOAT_CHECKED] == N_EPOCHS
    assert (cc.pos["q"] == 1).all() and np.allclose(cc.pos["lat"], LAT0)


def test_two_agreeing_bases_outvote_one_wrong_fix():
    cc = mb.cross_check([_sol("BAD", 30, 1, 1.0), _sol("G1", 40, 1), _sol("G2", 50, 1, 0.03)])
    assert cc.per_base["BAD"][mb.REJECTED] == N_EPOCHS
    assert cc.per_base["G1"][mb.CONFIRMED] == N_EPOCHS
    assert cc.primary == "G1"
    lat = cc.pos["lat"]
    assert np.allclose(lat, LAT0)                            # the wrong fix is not in the output


def test_lone_float_does_not_reject_a_fix():
    # a converging float far away says nothing about a fix (2026-11-14 parked run)
    cc = mb.cross_check([_sol("NEAR", 39, 1), _sol("FAR", 67, 2, 0.8)])
    assert cc.per_base["NEAR"][mb.REJECTED] == 0
    assert cc.per_base["NEAR"][mb.UNCHECKED] == N_EPOCHS
    assert (cc.pos["q"] == 1).all()
    assert any("only checked against float" in w for w in cc.warnings)


def test_mostly_wrong_run_of_fixes_is_thrown_out_whole():
    bad = _pos(1, 1.0)
    bad["lat"][:10] = LAT0                                   # a few epochs happen to agree
    others = [mb.BaseSolution("G1", 50, _pos(2)), mb.BaseSolution("G2", 60, _pos(2, 0.05))]
    cc = mb.cross_check([mb.BaseSolution("BAD", 30, bad), *others])
    assert cc.per_base["BAD"][mb.REJECTED] == N_EPOCHS
    assert (cc.pos["q"] == 2).all()


def test_confirmed_run_of_fixes_survives_clash_with_float_backed_wrong_fix():
    # 2026-11-14: LS02's long fix run is confirmed by CORB fixes in one part; in another part a
    # wrong LOYO fix, loosely backed by a CORB float, out-votes it. The run is kept whole.
    good = _pos(1)
    loyo = _pos(2, 0.0)
    loyo["q"][50:80], loyo["lat"][50:80] = 1, LAT0 + 0.3 / M_PER_DEG_LAT
    corb = _pos(2, 0.25)
    corb["q"][:30], corb["lat"][:30] = 1, LAT0 + 0.02 / M_PER_DEG_LAT
    cc = mb.cross_check([mb.BaseSolution("LS02", 39, good), mb.BaseSolution("LOYO", 51, loyo),
                         mb.BaseSolution("CORB", 67, corb)])
    assert cc.per_base["LS02"][mb.REJECTED] == 0
    assert cc.primary == "LS02" and (cc.pos["q"] == 1).all() and np.allclose(cc.pos["lat"], LAT0)


def test_one_base_is_kept_with_warning():
    cc = mb.cross_check([_sol("ONLY", 40, 1)])
    assert (cc.pos["q"] == 1).all() and cc.per_base["ONLY"][mb.UNCHECKED] == N_EPOCHS
    assert any("could not be cross-checked" in w for w in cc.warnings)
    assert cc.accuracy == {}


def test_gaps_filled_from_other_base():
    near = _pos(1)[:50]                                      # nearest base stops half way
    cc = mb.cross_check([mb.BaseSolution("NEAR", 30, near), _sol("FAR", 60, 1, 0.01)])
    assert len(cc.pos) == N_EPOCHS and (cc.pos["q"] == 1).all()
    assert np.all(np.diff(cc.pos["t"]) > 0)
