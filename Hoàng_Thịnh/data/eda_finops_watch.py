from __future__ import annotations

import json
from pathlib import Path

import matplotlib
from matplotlib.ticker import FuncFormatter
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "docs" / "assets" / "eda"

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def currency_formatter(value: float, _: int) -> str:
    if abs(value) >= 1000:
        return f"${value / 1000:.0f}k"
    return f"${value:.0f}"


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    cur = pd.read_csv(
        DATA_DIR / "cur_line_items.csv",
        parse_dates=[
            "bill_billing_period_start_date",
            "line_item_usage_start_date",
            "line_item_usage_end_date",
        ],
    )
    cur["usage_date"] = cur["line_item_usage_start_date"].dt.tz_localize(None).dt.normalize()

    ce = pd.read_csv(DATA_DIR / "cost_explorer_daily.csv", parse_dates=["date"])
    ce["date"] = ce["date"].dt.normalize()

    labels = pd.read_csv(DATA_DIR / "anomaly_labels_public.csv", parse_dates=["start_date", "end_date"])

    metrics_frames: dict[str, pd.DataFrame] = {}
    for path in sorted((DATA_DIR / "metrics").glob("*.csv")):
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        frame["usage_date"] = frame["timestamp"].dt.tz_localize(None).dt.normalize()
        metrics_frames[path.stem] = frame

    return cur, ce, labels, metrics_frames


def make_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def save_fig(fig: plt.Figure, name: str) -> None:
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / name, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_monthly_spend(cur: pd.DataFrame) -> pd.Series:
    monthly = (
        cur.groupby(cur["usage_date"].dt.to_period("M"))["line_item_unblended_cost"]
        .sum()
        .sort_index()
    )
    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = ["#315c8a", "#4f8f7a", "#bf7a30"]
    monthly.index = monthly.index.astype(str)
    bars = ax.bar(monthly.index, monthly.values, color=colors[: len(monthly)])
    ax.set_title("Monthly AWS Spend")
    ax.set_ylabel("Unblended cost")
    ax.yaxis.set_major_formatter(FuncFormatter(currency_formatter))
    for bar, value in zip(bars, monthly.values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 2500,
            f"${value:,.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    save_fig(fig, "monthly_spend.png")
    return monthly


def plot_daily_trend(cur: pd.DataFrame) -> pd.DataFrame:
    daily = (
        cur.groupby("usage_date")["line_item_unblended_cost"]
        .sum()
        .rename("daily_cost")
        .to_frame()
        .sort_index()
    )
    daily["rolling_7d"] = daily["daily_cost"].rolling(7, min_periods=1).mean()

    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(daily.index, daily["daily_cost"], color="#315c8a", linewidth=1.5, label="Daily total")
    ax.plot(daily.index, daily["rolling_7d"], color="#bf7a30", linewidth=2.0, label="7-day rolling mean")
    max_day = daily["daily_cost"].idxmax()
    max_value = daily.loc[max_day, "daily_cost"]
    ax.scatter([max_day], [max_value], color="#b04040", zorder=3)
    ax.annotate(
        f"Peak: ${max_value:,.0f}\n{max_day.date()}",
        xy=(max_day, max_value),
        xytext=(15, 12),
        textcoords="offset points",
        fontsize=9,
    )
    ax.set_title("Daily Spend Trend")
    ax.set_ylabel("Unblended cost")
    ax.yaxis.set_major_formatter(FuncFormatter(currency_formatter))
    ax.legend(frameon=False)
    save_fig(fig, "daily_spend_trend.png")
    return daily


def plot_account_share(cur: pd.DataFrame) -> pd.Series:
    account_spend = (
        cur.groupby("line_item_usage_account_name")["line_item_unblended_cost"]
        .sum()
        .sort_values()
    )
    fig, ax = plt.subplots(figsize=(8, 4.8))
    bars = ax.barh(account_spend.index, account_spend.values, color="#4f8f7a")
    ax.set_title("Spend by Linked Account")
    ax.set_xlabel("Unblended cost")
    ax.xaxis.set_major_formatter(FuncFormatter(currency_formatter))
    for bar, value in zip(bars, account_spend.values):
        ax.text(value + 2000, bar.get_y() + bar.get_height() / 2, f"${value:,.0f}", va="center", fontsize=9)
    save_fig(fig, "account_share.png")
    return account_spend.sort_values(ascending=False)


def plot_service_share(cur: pd.DataFrame) -> pd.Series:
    service_spend = (
        cur.groupby("line_item_product_code")["line_item_unblended_cost"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .sort_values()
    )
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    bars = ax.barh(service_spend.index, service_spend.values, color="#315c8a")
    ax.set_title("Top 10 Services by Spend")
    ax.set_xlabel("Unblended cost")
    ax.xaxis.set_major_formatter(FuncFormatter(currency_formatter))
    for bar, value in zip(bars, service_spend.values):
        ax.text(value + 1400, bar.get_y() + bar.get_height() / 2, f"${value:,.0f}", va="center", fontsize=8)
    save_fig(fig, "service_share.png")
    return service_spend.sort_values(ascending=False)


def plot_public_label_events(ce: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    label_rows: list[dict[str, object]] = []
    fig, axes = plt.subplots(nrows=3, ncols=1, figsize=(11, 10), sharex=False)
    axes = list(axes)

    for ax, (_, label_row) in zip(axes, labels.iterrows()):
        pair = ce[
            (ce["linked_account_id"] == label_row["linked_account_id"])
            & (ce["service_code"] == label_row["service"])
        ].copy()
        pair = pair.sort_values("date")

        event_mask = (pair["date"] >= label_row["start_date"]) & (pair["date"] <= label_row["end_date"])
        event = pair.loc[event_mask]
        pre14 = pair.loc[pair["date"] < label_row["start_date"]].tail(14)

        label_rows.append(
            {
                "anomaly_id": label_row["anomaly_id"],
                "label": label_row["label"],
                "anomaly_type": label_row["anomaly_type"],
                "linked_account_name": label_row["linked_account_name"],
                "service": label_row["service"],
                "event_days": int(len(event)),
                "event_total_cost": round(float(event["unblended_cost"].sum()), 2),
                "event_mean_cost": round(float(event["unblended_cost"].mean()), 2),
                "pre14_mean_cost": round(float(pre14["unblended_cost"].mean()), 2)
                if not pre14.empty
                else None,
            }
        )

        ax.plot(pair["date"], pair["unblended_cost"], color="#315c8a", linewidth=1.8)
        ax.axvspan(label_row["start_date"], label_row["end_date"], color="#d8c4a2", alpha=0.4)
        ax.set_title(
            f"{label_row['anomaly_id']} | {label_row['linked_account_name']} | "
            f"{label_row['service']} | {label_row['label']}"
        )
        ax.set_ylabel("Daily cost")
        ax.yaxis.set_major_formatter(FuncFormatter(currency_formatter))
        ax.annotate(
            f"{label_row['anomaly_type']}\n{label_row['start_date'].date()} to {label_row['end_date'].date()}",
            xy=(label_row["start_date"], event["unblended_cost"].max() if not event.empty else pair["unblended_cost"].max()),
            xytext=(10, 8),
            textcoords="offset points",
            fontsize=8,
        )

    axes[-1].set_xlabel("Date")
    save_fig(fig, "public_label_events.png")
    return pd.DataFrame(label_rows)


def plot_metrics_inventory(metrics_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for name, frame in metrics_frames.items():
        row = {
            "dataset": name,
            "rows": int(len(frame)),
            "resources": int(frame["resource_id"].nunique()),
            "days": int(frame["usage_date"].nunique()),
        }
        for label_name, label_count in frame["label"].value_counts().to_dict().items():
            row[f"label_{label_name}"] = int(label_count)
        rows.append(row)

    metrics_inventory = pd.DataFrame(rows).sort_values("rows", ascending=False)

    fig, axes = plt.subplots(ncols=2, figsize=(11, 4.4))
    axes[0].bar(metrics_inventory["dataset"], metrics_inventory["rows"], color="#315c8a")
    axes[0].set_title("Metrics Rows by Dataset")
    axes[0].set_ylabel("Rows")
    axes[0].tick_params(axis="x", rotation=30)

    axes[1].bar(metrics_inventory["dataset"], metrics_inventory["resources"], color="#4f8f7a")
    axes[1].set_title("Distinct Resources by Dataset")
    axes[1].set_ylabel("Resources")
    axes[1].tick_params(axis="x", rotation=30)
    save_fig(fig, "metrics_inventory.png")

    return metrics_inventory


def build_summary(
    cur: pd.DataFrame,
    ce: pd.DataFrame,
    labels: pd.DataFrame,
    metrics_frames: dict[str, pd.DataFrame],
    monthly: pd.Series,
    daily: pd.DataFrame,
    accounts: pd.Series,
    services: pd.Series,
    label_summary: pd.DataFrame,
    metrics_inventory: pd.DataFrame,
) -> dict[str, object]:
    team_blank = cur["resource_tags_user_team"].fillna("").eq("")
    ce_estimated = ce["is_estimated"].astype(str).str.lower().eq("true")

    cur_daily = cur.groupby("usage_date")["line_item_unblended_cost"].sum().round(4)
    ce_daily = ce.groupby("date")["unblended_cost"].sum().round(4)
    diff = (cur_daily - ce_daily).round(4)

    metric_keys = pd.concat(
        [frame[["resource_id", "usage_date"]] for frame in metrics_frames.values()],
        ignore_index=True,
    )

    summary = {
        "files": {
            "cur_line_items_rows": int(len(cur)),
            "cost_explorer_rows": int(len(ce)),
            "public_labels_rows": int(len(labels)),
            "metrics_total_rows": int(sum(len(frame) for frame in metrics_frames.values())),
        },
        "coverage": {
            "date_start": str(cur["usage_date"].min().date()),
            "date_end": str(cur["usage_date"].max().date()),
            "linked_accounts": int(cur["line_item_usage_account_name"].nunique()),
            "services": int(cur["line_item_product_code"].nunique()),
            "resources": int(cur["line_item_resource_id"].nunique()),
            "regions": int(cur["product_region_code"].nunique()),
            "usage_types": int(cur["line_item_usage_type"].nunique()),
            "operations": int(cur["line_item_operation"].nunique()),
        },
        "cost": {
            "cur_total": round(float(cur["line_item_unblended_cost"].sum()), 2),
            "ce_total": round(float(ce["unblended_cost"].sum()), 2),
            "monthly_totals": {month: round(float(value), 2) for month, value in monthly.items()},
            "daily_min": round(float(daily["daily_cost"].min()), 2),
            "daily_median": round(float(daily["daily_cost"].median()), 2),
            "daily_max": round(float(daily["daily_cost"].max()), 2),
            "peak_day": str(daily["daily_cost"].idxmax().date()),
        },
        "concentration": {
            "top_accounts": {name: round(float(value), 2) for name, value in accounts.items()},
            "top_services": {name: round(float(value), 2) for name, value in services.items()},
            "top2_account_share_pct": round(float(accounts.head(2).sum() / cur["line_item_unblended_cost"].sum() * 100), 2),
            "top3_service_share_pct": round(float(services.head(3).sum() / cur["line_item_unblended_cost"].sum() * 100), 2),
        },
        "data_quality": {
            "untagged_rows": int(team_blank.sum()),
            "untagged_resources": int(cur.loc[team_blank, "line_item_resource_id"].nunique()),
            "untagged_cost": round(float(cur.loc[team_blank, "line_item_unblended_cost"].sum()), 2),
            "untagged_share_pct": round(float(cur.loc[team_blank, "line_item_unblended_cost"].sum() / cur["line_item_unblended_cost"].sum() * 100), 2),
            "owner_blank_pct": round(
                float(cur["resource_tags_user_owner"].fillna("").eq("").mean() * 100),
                2,
            ),
            "ce_estimated_days": sorted(str(value.date()) for value in ce.loc[ce_estimated, "date"].drop_duplicates()),
            "ce_estimated_cost": round(float(ce.loc[ce_estimated, "unblended_cost"].sum()), 2),
            "cur_ce_max_abs_daily_diff": round(float(diff.abs().max()), 4),
        },
        "public_labels": label_summary.to_dict(orient="records"),
        "metrics": {
            "total_rows": int(len(metric_keys)),
            "total_unique_keys": int(len(metric_keys.drop_duplicates())),
            "cur_unique_keys": int(len(cur[["line_item_resource_id", "usage_date"]].drop_duplicates())),
            "metrics_inventory": metrics_inventory.to_dict(orient="records"),
        },
    }
    return summary


def write_outputs(summary: dict[str, object], label_summary: pd.DataFrame, metrics_inventory: pd.DataFrame) -> None:
    (OUTPUT_DIR / "public_label_summary.csv").write_text(label_summary.to_csv(index=False), encoding="utf-8")
    (OUTPUT_DIR / "metrics_inventory.csv").write_text(metrics_inventory.to_csv(index=False), encoding="utf-8")
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(make_json_safe(summary), indent=2, allow_nan=False),
        encoding="utf-8",
    )


def make_json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {key: make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def main() -> None:
    make_output_dir()
    cur, ce, labels, metrics_frames = load_inputs()

    monthly = plot_monthly_spend(cur)
    daily = plot_daily_trend(cur)
    accounts = plot_account_share(cur)
    services = plot_service_share(cur)
    label_summary = plot_public_label_events(ce, labels)
    metrics_inventory = plot_metrics_inventory(metrics_frames)

    summary = build_summary(
        cur=cur,
        ce=ce,
        labels=labels,
        metrics_frames=metrics_frames,
        monthly=monthly,
        daily=daily,
        accounts=accounts,
        services=services,
        label_summary=label_summary,
        metrics_inventory=metrics_inventory,
    )
    write_outputs(summary, label_summary, metrics_inventory)
    print(f"EDA outputs written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
