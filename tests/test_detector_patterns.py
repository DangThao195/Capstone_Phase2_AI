import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_detector", ROOT / "verify_detector.py")
verify_detector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_detector)


def test_stable_low_cost_flagged_for_idle_like_series():
    series = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=7, freq="D"),
            "cost": [8.2, 8.7, 8.4, 8.9, 9.1, 8.8, 9.0],
            "usage": [100, 102, 101, 103, 104, 105, 106],
            "tag_missing": [False] * 7,
        }
    )
    series = verify_detector.add_robust_features(series)
    assert bool(series["stable_low_cost"].iloc[-1]) is True


def test_scenario_window_suppresses_benign_resource_during_documented_period():
    assert verify_detector.is_benign_resource("migration-egress-onetime", "AWSDataTransfer", pd.Timestamp("2026-03-28")) is True
    assert verify_detector.is_benign_resource("migration-egress-onetime", "AWSDataTransfer", pd.Timestamp("2026-03-31")) is True
    assert verify_detector.is_benign_resource("migration-egress-onetime", "AWSDataTransfer", pd.Timestamp("2026-04-01")) is False


def test_benign_scenario_resources_are_marked_benign():
    mask = verify_detector.resource_features["resource_id"] == "i-0flashsale-autoscale"
    assert bool(verify_detector.resource_features.loc[mask, "is_benign"].any()) is True


def test_scenario_pack_marks_benign_resources_without_keyword_patterns():
    assert verify_detector.is_benign_resource("i-0flashsale-autoscale", "AmazonEC2", pd.Timestamp("2026-05-24")) is True


# --- Scenario tests cho A2, A5, A6, A7 ---

def test_a5_nat_absolute_jump_spike_detected():
    """A5: NAT Gateway nhảy từ $0 lên $543/ngày — baseline ~0, ratio = inf bị clean.
    Phải bắt bằng absolute_jump_spike (delta >= $200 từ baseline thấp)."""
    # 7 ngày $0, rồi nhảy $543
    series = pd.DataFrame(
        {
            "date": pd.date_range("2026-05-05", periods=10, freq="D"),
            "cost": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 543.2, 542.8, 515.8],
            "usage": [0.0] * 7 + [54322.0, 54287.0, 51583.0],
            "tag_missing": [False] * 10,
        }
    )
    series = verify_detector.add_robust_features(series)
    # Ngày jump (index 7): cost_abs_jump phải >= 200
    assert float(series["cost_abs_jump"].iloc[7]) >= 200.0, (
        f"Expected cost_abs_jump >= 200 on spike day, got {series['cost_abs_jump'].iloc[7]}"
    )


def test_a6_cloudwatch_absolute_jump_spike_detected():
    """A6: CloudWatch ~$0 -> $260/ngày — giống A5, baseline ~0."""
    series = pd.DataFrame(
        {
            "date": pd.date_range("2026-04-21", periods=10, freq="D"),
            "cost": [0.5, 0.3, 0.4, 0.6, 0.5, 0.4, 0.3, 263.8, 272.7, 246.9],
            "usage": [1.0] * 7 + [527.6, 545.5, 493.8],
            "tag_missing": [False] * 10,
        }
    )
    series = verify_detector.add_robust_features(series)
    assert float(series["cost_abs_jump"].iloc[7]) >= 200.0, (
        f"Expected cost_abs_jump >= 200 on CloudWatch spike day, got {series['cost_abs_jump'].iloc[7]}"
    )


def test_a2_rds_idle_detected_by_stable_cost_pattern():
    """A2: RDS orphan — usage = Hrs (22-25/ngày), cost $27/ngày ổn định 10+ tuần.
    idle_like_rds phải True sau 14 ngày (CV thấp, cost ổn định)."""
    import numpy as np
    rng = np.random.default_rng(42)
    costs = 27.0 + rng.uniform(-1.0, 1.0, 20)   # ~$27 ± $1, rất ổn định
    usages = 23.0 + rng.uniform(-0.5, 0.5, 20)  # ~23 Hrs (RDS billing)
    series = pd.DataFrame(
        {
            "date": pd.date_range("2026-03-20", periods=20, freq="D"),
            "cost": costs,
            "usage": usages,
            "tag_missing": [False] * 20,
        }
    )
    series = verify_detector.add_robust_features(series)
    # Sau 14 ngày rolling phải có idle_like_rds = True
    assert bool(series["idle_like_rds"].iloc[-1]) is True, (
        f"Expected idle_like_rds=True for stable RDS cost, CV={series['cost'].std()/series['cost'].mean():.4f}"
    )


def test_a7_dynamodb_gradual_drift_mom_detected():
    """A7: DynamoDB WCU drift — cost leo từ $62 -> $318 trong 2 tháng.
    mom_ratio phải >= 1.5 ở cuối May so với đầu April."""
    # Apr: $62-$130, May: $280-$320
    apr_costs = [62 + i * 4.5 for i in range(30)]   # leo đều từ $62 -> $197
    may_costs = [200 + i * 4.0 for i in range(31)]  # leo tiếp từ $200 -> $320
    all_costs = apr_costs + may_costs
    series = pd.DataFrame(
        {
            "date": pd.date_range("2026-04-01", periods=61, freq="D"),
            "cost": all_costs,
            "usage": [95000 + i * 12000 for i in range(61)],
            "tag_missing": [False] * 61,
        }
    )
    series = verify_detector.add_robust_features(series)
    # Cuối May, mom_ratio phải >= 1.5 (May mean ~$260 vs Apr mean ~$130)
    last_mom = float(series["mom_ratio"].iloc[-1])
    assert last_mom >= 1.5, f"Expected mom_ratio >= 1.5 for DynamoDB drift, got {last_mom:.3f}"


def test_benign_spike_not_flagged_when_baseline_normal():
    """FP guard: resource có baseline bình thường $100/ngày, tăng 1.3x không phải spike."""
    series = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=14, freq="D"),
            "cost": [100.0] * 10 + [130.0, 128.0, 132.0, 129.0],
            "usage": [1000.0] * 14,
            "tag_missing": [False] * 14,
        }
    )
    series = verify_detector.add_robust_features(series)
    # cost_abs_jump phải thấp (baseline = $100, jump chỉ $30)
    assert float(series["cost_abs_jump"].iloc[-1]) < 200.0
    # stable_low_cost phải False (cost không phải $6-$15)
    assert bool(series["stable_low_cost"].iloc[-1]) is False
