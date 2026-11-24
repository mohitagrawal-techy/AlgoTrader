"""The owner's own config files, not the pinned copy the other tests use, must still load."""

import yaml
from algoverse.backtest.config import load_backtest_config
from algoverse.evaluation.config import load_event_calendar, load_scoring_config
from algoverse.risk.config import load_risk_config
from algoverse.risk.costs import load_cost_model
from algoverse.settings import load_universe
from conftest import CONFIG_DIR, LIVE_CONFIG_DIR, TEST_SETTINGS


def test_live_config_files_load():
    assert load_risk_config(LIVE_CONFIG_DIR).capital > 0
    load_backtest_config(LIVE_CONFIG_DIR)
    load_scoring_config(LIVE_CONFIG_DIR)
    load_cost_model(LIVE_CONFIG_DIR)
    load_event_calendar(LIVE_CONFIG_DIR)
    assert load_universe(LIVE_CONFIG_DIR).underlyings


def test_tests_read_a_pinned_copy():
    assert CONFIG_DIR != LIVE_CONFIG_DIR
    for name, values in TEST_SETTINGS.items():
        pinned = yaml.safe_load((CONFIG_DIR / name).read_text())
        assert {key: pinned[key] for key in values} == values
    assert load_risk_config(CONFIG_DIR).capital == 500000
