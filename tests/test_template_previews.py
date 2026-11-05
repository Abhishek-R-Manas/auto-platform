"""Preview profiles describe the recipe, not a shared decorative icon or market quote."""
import math
import pytest
from app.workbench.templates import RECIPES,catalog
from app.finance import option_value


def profiles():return {row['id']:row for row in catalog()}


def value(preview,spot):return next(p['pnl'] for p in preview['points'] if p['spot']==spot)


def test_call_and_put_previews_match_contract_payoffs():
    rows=profiles();call=rows['long_call']['preview'];put=rows['long_put']['preview']
    call_entry=option_value(100,100,30/365,.25,'CE',.065)
    put_entry=option_value(100,100,30/365,.25,'PE',.065)
    assert value(call,70)==pytest.approx(-call_entry) and value(call,130)==pytest.approx(30-call_entry)
    assert value(put,70)==pytest.approx(30-put_entry) and value(put,130)==pytest.approx(-put_entry)
    assert value(call,70)<value(call,130) and value(put,70)>value(put,130)


def test_straddle_and_condor_profiles_have_the_correct_centres_and_tails():
    rows=profiles();long=rows['long_straddle']['preview'];short=rows['short_straddle']['preview'];condor=rows['iron_condor']['preview']
    assert value(long,100)<value(long,70) and value(long,100)<value(long,130)
    assert value(short,100)>value(short,70) and value(short,100)>value(short,130)
    assert value(condor,100)>0 and value(condor,70)<0 and value(condor,130)<0
    assert value(condor,70)==pytest.approx(value(condor,80)) and value(condor,120)==pytest.approx(value(condor,130))


def test_vertical_and_box_bounds_are_reflected_without_inventing_extra_shapes():
    rows=profiles();spread=rows['bull_call']['preview'];box=rows['long_box']['preview']
    assert value(spread,70)==pytest.approx(value(spread,80))
    assert value(spread,120)==pytest.approx(value(spread,130))
    assert max(p['pnl'] for p in box['points'])-min(p['pnl'] for p in box['points'])<1e-7


def test_calendar_keeps_later_contract_time_value_and_is_labeled_as_model():
    p=profiles()['call_calendar']['preview'];near=option_value(100,100,30/365,.25,'CE',.065);far=option_value(100,100,60/365,.25,'CE',.065)
    assert p['basis']=='MODEL_AT_FIRST_EXPIRY' and not p['market_data']
    assert value(p,100)==pytest.approx(near-far+option_value(100,100,30/365,.25,'CE',.065))
    assert value(p,100)!=pytest.approx(near-far)


def test_every_template_has_finite_recipe_specific_preview_and_structure():
    rows=profiles();assert set(rows)==set(RECIPES) and len(rows)==51
    for row in rows.values():
        p=row['preview'];assert row['family'] and p['market_data'] is False
        assert p['basis']==('MODEL_AT_FIRST_EXPIRY' if row['multi_expiry'] else 'ILLUSTRATIVE_EXPIRY')
        assert all(math.isfinite(v) for point in p['points'] for v in point.values())
        assert [point['spot'] for point in p['points']]==sorted(set(point['spot'] for point in p['points']))
    assert rows['bull_call']['family']=='Vertical spreads' and rows['iron_condor']['family']=='Condors'
    assert rows['call_calendar']['family']=='Calendars & diagonals'
