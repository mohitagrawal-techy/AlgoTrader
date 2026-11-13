import pytest
from algoverse.backtest.walkforward import load_services
from algoverse.lab.spec import Budget, StrategySpec, apply_specs, signature, with_budget
from conftest import CONFIG_DIR
from pydantic import ValidationError
from test_lab import NOW, SPREAD, fresh, lab_dirs  # noqa: F401


def spec(**budget) -> StrategySpec:
    return StrategySpec.model_validate({"id": "s1", **SPREAD, "budget": budget})


def test_default_budget_keeps_the_account_rupee_limits():
    base = load_services(CONFIG_DIR)
    risk = with_budget(apply_specs(base, [spec()]), spec()).risk
    account = base.risk
    assert risk.capital == 100_000
    # 5% of the budget is what 1% of the whole account allowed.
    assert risk.amount(risk.max_loss_per_trade_pct) == account.amount(
        account.max_loss_per_trade_pct
    )
    assert risk.max_open_positions == 5
    # Greek and stress limits stay at the account's rupee amounts.
    for field in ("max_vega_pct", "max_gamma_1pct_pct", "max_stress_loss_pct"):
        assert risk.amount(getattr(risk, field)) == pytest.approx(
            account.amount(getattr(account, field))
        )
    assert risk.capital * risk.max_delta_notional_multiple == pytest.approx(
        account.capital * account.max_delta_notional_multiple
    )


def test_a_strategy_budget_sets_its_own_limits():
    tight = spec(capital=200_000, risk_per_trade_pct=1.5, max_open=4)
    risk = with_budget(apply_specs(load_services(CONFIG_DIR), [tight]), tight).risk
    assert risk.capital == 200_000 and risk.max_loss_per_trade_pct == 1.5
    assert risk.max_open_positions == 4
    # Four positions at full risk fit the open-risk limit.
    assert risk.max_open_risk_pct >= 1.5 * 4
    assert tight.budget.risk_per_trade == 3_000


def test_old_strategies_keep_their_signature_and_budgets_are_bounded():
    assert signature(spec()) == signature(StrategySpec.model_validate({"id": "s1", **SPREAD}))
    with pytest.raises(ValidationError):
        Budget(capital=5_000)
    with pytest.raises(ValidationError):
        Budget(max_open=0)


def test_a_result_from_another_budget_is_stale(lab_dirs, tmp_path):  # noqa: F811
    lab = fresh(lab_dirs, tmp_path)
    saved = lab.save(SPREAD, NOW)
    result = lab.run_backtest([saved.id], NOW)[0]
    assert result["budget"] == saved.budget.model_dump()
    assert lab.result(saved) is not None
    smaller = lab.save(
        {**saved.model_dump(mode="json"), "budget": {"capital": 50_000, "risk_per_trade_pct": 5}},
        NOW,
    )
    assert lab.result(smaller) is None
    page = lab.backtest_detail(saved.id)
    assert page["capital"] == 50_000
