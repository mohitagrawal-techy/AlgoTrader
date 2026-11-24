"""Every test the lab has run, for the test history page.

The trial registry records each run's figures: lab tests (training and test periods), tuner and
ladder checks of one change, and walk-forward folds. A strategy's result file holds only its
latest test, so each lab test is also archived here with its verdict and the exact rules that
were tested, which lets an old test be opened and compared with the strategy as it is now.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from algoverse.backtest.registry import Registry

KINDS = {
    "lab": "Lab test",
    "tune": "Tuner check",
    "ladder": "Ladder check",
    "walk_forward": "Walk-forward",
}
FAMILY = {
    "lab_train": "lab",
    "lab_test": "lab",
    "tune_validate": "tune",
    "ladder_validate": "ladder",
    "walk_forward_train": "walk_forward",
    "walk_forward_test": "walk_forward",
}
PERIODS = {
    "lab_train": "Training",
    "lab_test": "Test (unseen)",
    "tune_validate": "Unseen check",
    "ladder_validate": "Unseen check",
    "walk_forward_test": "Unseen folds",
}
ENTRY_ID = re.compile(r"^\d{8}-\d{6}-[a-z0-9_]{1,48}$")
# The parts of a result worth keeping for each test; trade detail stays in the latest result.
KEPT = ("verdict", "windows", "train", "test", "test_2x", "evidence", "config_hash")


def _folder(data_dir: Path) -> Path:
    return Path(data_dir) / "lab" / "history"


def entry_id(evaluated_at: str, strategy_id: str) -> str:
    return f"{datetime.fromisoformat(evaluated_at):%Y%m%d-%H%M%S}-{strategy_id}"


def archive(data_dir: Path, result: dict, spec: dict) -> str:
    """Keep one lab test: its figures, verdict and the rules it tested."""
    name = entry_id(result["evaluated_at"], result["strategy_id"])
    entry = {
        "id": name,
        "strategy_id": result["strategy_id"],
        "name": spec.get("name"),
        "evaluated_at": result["evaluated_at"],
        "signature": result.get("signature"),
        "spec": spec,
        **{key: result.get(key) for key in KEPT},
    }
    folder = _folder(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry, default=str))
    os.replace(tmp, path)
    return name


def read_entry(data_dir: Path, name: str) -> dict | None:
    if not ENTRY_ID.match(name):
        return None
    path = _folder(data_dir) / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _archived(data_dir: Path) -> dict[str, dict]:
    found = {}
    for path in _folder(data_dir).glob("*.json"):
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        found[entry["id"]] = {
            "name": entry.get("name"),
            "verdict": entry.get("verdict"),
            "signature": entry.get("signature"),
        }
    return found


def _readable(strategy_id: str) -> str:
    """A name for a strategy that is no longer stored, such as a tuner's variant."""
    text = re.sub(r"_[0-9a-f]{6}$", "", strategy_id or "")
    text = re.sub(r"_tuned_(\d+)$", r" (tuned, round \1)", text)
    return text.replace("tpl_", "").replace("_", " ").strip().capitalize() or "Unknown"


def runs(data_dir: Path, names: dict[str, str], signatures: dict[str, str]) -> list[dict]:
    """Trials grouped into runs, newest first. `names` and `signatures` describe the stored
    strategies, so a run can say whether the strategy has changed since."""
    archived = _archived(data_dir)
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []
    for trial in Registry(data_dir).all_trials():
        family = FAMILY.get(trial["kind"])
        if family is None:
            continue
        params = trial["params"]
        strategy = params.get("strategy") or ""
        # Walk-forward folds of one run share its timestamp; other runs are one strategy each.
        key = (
            trial["ts"],
            family,
            "" if family == "walk_forward" else strategy,
            params.get("step"),
        )
        if key not in grouped:
            order.append(key)
            grouped[key] = {
                "ts": trial["ts"],
                "kind": family,
                "kind_label": KINDS[family],
                "strategy_id": strategy or None,
                "step": params.get("step"),
                "signature": params.get("signature"),
                "periods": {},
            }
        run = grouped[key]
        label = PERIODS.get(trial["kind"])
        if label is None:
            run["settings_tried"] = run.get("settings_tried", 0) + 1
            continue
        period = run["periods"].setdefault(
            label,
            {
                "label": label,
                "start": trial["start"],
                "end": trial["end"],
                "trades": 0,
                "net_pnl": 0.0,
                "sharpe": trial["sharpe"],
                "folds": 0,
            },
        )
        period["start"] = min(period["start"], trial["start"])
        period["end"] = max(period["end"], trial["end"])
        period["trades"] += trial["trades"] or 0
        period["net_pnl"] += trial["net_pnl"] or 0.0
        period["folds"] += 1
        if period["folds"] > 1:
            period["sharpe"] = None
    found = []
    for key in order:
        run = grouped[key]
        strategy = run["strategy_id"]
        if run["kind"] == "lab" and strategy:
            name = entry_id(run["ts"], strategy)
            entry = archived.get(name)
            if entry:
                run["entry_id"] = name
                run["verdict"] = entry["verdict"]
        if strategy:
            stored = names.get(strategy)
            run["strategy_name"] = stored or (
                archived.get(run.get("entry_id", ""), {}).get("name") or _readable(strategy)
            )
            run["exists"] = stored is not None
            run["changed_since"] = (
                stored is not None
                and run.get("signature") is not None
                and signatures.get(strategy) != run["signature"]
            )
        else:
            run["strategy_name"] = "Idea engine settings"
            run["exists"] = False
            run["changed_since"] = False
        periods = list(run.pop("periods").values())
        periods.sort(
            key=lambda p: (
                ("Training", "Test (unseen)").index(p["label"])
                if p["label"] in ("Training", "Test (unseen)")
                else 2
            )
        )
        run["periods"] = periods
        found.append(run)
    return found


def summarise(found: list[dict]) -> dict:
    counts = {kind: 0 for kind in KINDS}
    for run in found:
        counts[run["kind"]] += 1
    return {
        "runs": len(found),
        "by_kind": [{"kind": k, "label": KINDS[k], "runs": counts[k]} for k in KINDS],
        "strategies": len({run["strategy_id"] for run in found if run["strategy_id"]}),
        "first": found[-1]["ts"] if found else None,
        "last": found[0]["ts"] if found else None,
    }
