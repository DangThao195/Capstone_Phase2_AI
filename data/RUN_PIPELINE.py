# RUN_PIPELINE.py
# Entry point chay toan bo pipeline:
#   FEATURE (Step 1-3) -> DETECT (Step 4-7) -> ROOT_CAUSE -> ACTION
#
# Cach chay:
#   python RUN_PIPELINE.py                    # dry_run, mock Bedrock
#   python RUN_PIPELINE.py --live             # live execution (can Bedrock + DynamoDB)
#   python RUN_PIPELINE.py --dry-run          # ro rang dry_run
#   python RUN_PIPELINE.py --limit 5          # chi chay 5 anomaly dau
#   python RUN_PIPELINE.py --env dev          # chi chay anomaly thuoc env dev
#
# Bien moi truong:
#   BEDROCK_MOCK=true     -> khong can AWS credentials (mac dinh khi test)
#   BEDROCK_MOCK=false    -> goi Bedrock that (can aws configure)
#   FINOPS_DRY_RUN=true   -> chi in CLI command, khong thuc thi
#   FINOPS_DRY_RUN=false  -> thuc thi that (can IAM permissions)
#   SLACK_WEBHOOK_URL     -> URL Slack Incoming Webhook de nhan alert
#   BEDROCK_REGION        -> region Bedrock (mac dinh us-east-1)
#   DYNAMO_REGION         -> region DynamoDB (mac dinh us-east-1)

import argparse
import json
import logging
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# =============================================================================
# Parse CLI args
# =============================================================================
def parse_args():
    p = argparse.ArgumentParser(description="FinOps AI Detection + RCA + Action Pipeline")
    p.add_argument("--live",     action="store_true", help="Thuc thi AWS CLI that (mac dinh: dry-run)")
    p.add_argument("--dry-run",  action="store_true", help="Chi in lenh, khong thuc thi (mac dinh)")
    p.add_argument("--limit",    type=int, default=None, help="Gioi han so anomaly xu ly (None=tat ca)")
    p.add_argument("--env",      type=str, default=None, help="Chi xu ly env cu the: prod|staging|dev|ml-research|data-analytics")
    p.add_argument("--mock",     action="store_true", default=True, help="Dung mock Bedrock (mac dinh True)")
    p.add_argument("--no-mock",  action="store_true", help="Tat mock, goi Bedrock that")
    p.add_argument("--slack",    type=str, default=None, help="Slack webhook URL")
    return p.parse_args()


# =============================================================================
# Step 1-7: Run XGBoost Detection Pipeline
# =============================================================================
def run_detection_pipeline():
    logger.info("=" * 65)
    logger.info("PHASE 1: XGBOOST DETECTION PIPELINE")
    logger.info("=" * 65)

    from FEATURE import load_and_merge, temporal_split, build_xy
    from DETECT  import walk_forward_cv, train_final_model, optimize_threshold, evaluate_model

    DATA_DIR = "."

    df_merged                 = load_and_merge(DATA_DIR)
    df_train_raw, df_test_raw = temporal_split(df_merged)
    X_train, y_train, X_test, y_test, train_stats, features = build_xy(df_train_raw, df_test_raw)

    cv_scores, cv_val_results         = walk_forward_cv(df_train_raw)
    model, _                          = train_final_model(X_train, y_train, cv_scores)
    best_threshold, _                 = optimize_threshold(cv_val_results)
    test_pred, test_prob, evaluation_df = evaluate_model(model, X_test, y_test, best_threshold)

    # Gan lai cac cot metadata tu df_test_raw
    df_test_reset = df_test_raw.reset_index(drop=True)
    evaluation_df = evaluation_df.reset_index(drop=True)

    meta_cols = [
        "line_item_resource_id", "line_item_product_code",
        "resource_tags_user_environment", "resource_tags_user_owner",
        "resource_tags_user_team", "clean_date", "line_item_unblended_cost",
        "line_item_usage_amount",
    ]
    for col in meta_cols:
        if col in df_test_reset.columns:
            evaluation_df[col] = df_test_reset[col].values

    logger.info("Detection complete. Total test rows: %d", len(evaluation_df))
    logger.info("Anomalies detected (Prediction=1): %d", (evaluation_df["Prediction"] == 1).sum())
    logger.info("CV F1-macro: %.4f +/- %.4f", np.mean(cv_scores), np.std(cv_scores))

    return evaluation_df, df_test_raw


# =============================================================================
# Convert evaluation_df row -> anomaly_record dict
# =============================================================================
def row_to_record(row: pd.Series, df_test_raw: pd.DataFrame, idx: int) -> dict:
    """Chuyen 1 dong tu evaluation_df sang anomaly_record cho RCA pipeline."""
    raw_row = df_test_raw.iloc[idx] if idx < len(df_test_raw) else pd.Series()

    owner = raw_row.get("resource_tags_user_owner", None)
    if pd.isna(owner) if owner is not None else True:
        owner = None

    env = str(raw_row.get("resource_tags_user_environment",
              row.get("resource_tags_user_environment", "dev"))).lower()

    usage_amount = float(raw_row.get("line_item_usage_amount",
                         row.get("line_item_usage_amount", 0)))
    usage_density = round(usage_amount / 24.0, 4) if usage_amount > 0 else 0.0

    return {
        "resource_id":               str(raw_row.get("line_item_resource_id",
                                         row.get("line_item_resource_id", f"resource-{idx}"))),
        "environment":               env,
        "confidence_score":          float(row.get("Probability", 0)),
        "line_item_product_code":    str(raw_row.get("line_item_product_code",
                                         row.get("line_item_product_code", "unknown"))),
        "line_item_unblended_cost":  float(raw_row.get("line_item_unblended_cost",
                                           row.get("line_item_unblended_cost", 0))),
        "cost_ratio_to_7d_avg":      float(row.get("cost_ratio_to_7d_avg", 1.0)),
        "usage_density_24h":         usage_density,
        "cpu_mean":                  float(row.get("cpu_mean", 0)),
        "resource_tags_user_owner":  owner,
        "resource_tags_user_team":   str(raw_row.get("resource_tags_user_team",
                                         row.get("resource_tags_user_team", "unknown"))),
        "absolute_cost_spike":       float(row.get("absolute_cost_spike", 0)),
        "rolling_7d_avg":            float(row.get("rolling_7d_avg", 0)),
    }


# =============================================================================
# Step 8-9: Run RCA + Action per anomaly (with retry + stop-on-failure)
# =============================================================================
def run_rca_action_pipeline(
    evaluation_df: pd.DataFrame,
    df_test_raw:   pd.DataFrame,
    dry_run:       bool  = True,
    limit:         int   = None,
    filter_env:    str   = None,
    slack_webhook: str   = None,
    max_retries:   int   = 3,      # So lan thu lai toi da khi gap loi
    stop_on_fail:  bool  = True,   # Dung chuong trinh neu that bai sau max_retries
) -> list:
    logger.info("=" * 65)
    logger.info("PHASE 2: RCA + ACTION PIPELINE")
    logger.info("=" * 65)

    from ROOT_CAUSE import analyse_root_cause
    from ACTION     import run_action_pipeline

    anomalies = evaluation_df[evaluation_df["Prediction"] == 1].copy()

    if filter_env:
        env_col = "resource_tags_user_environment"
        if env_col in anomalies.columns:
            anomalies = anomalies[anomalies[env_col].str.lower() == filter_env.lower()]
        logger.info("Filtered to env=%s: %d anomalies", filter_env, len(anomalies))

    if limit:
        anomalies = anomalies.head(limit)
        logger.info("Limiting to %d anomalies", limit)

    logger.info("Processing %d anomalies (dry_run=%s, max_retries=%d)...",
                len(anomalies), dry_run, max_retries)

    results       = []
    success       = 0
    errors        = 0
    consecutive_failures = 0   # dem so lan loi lien tiep

    for i, (df_idx, row) in enumerate(anomalies.iterrows()):
        test_idx = list(evaluation_df.index).index(df_idx) if df_idx in evaluation_df.index else i
        record   = row_to_record(row, df_test_raw, test_idx)

        logger.info(
            "[%d/%d] resource=%s | env=%s | confidence=%.2f | cost=$%.2f",
            i + 1, len(anomalies),
            record["resource_id"][:40],
            record["environment"],
            record["confidence_score"],
            record["line_item_unblended_cost"],
        )

        # ── Retry loop ────────────────────────────────────────────────────────
        last_error = None
        attempt    = 0
        output     = None

        for attempt in range(1, max_retries + 1):
            try:
                rca    = analyse_root_cause(record)
                output = run_action_pipeline(
                    record        = record,
                    rca           = rca,
                    slack_webhook = slack_webhook,
                    dry_run       = dry_run,
                )
                last_error = None
                break   # thanh cong, thoat khoi retry loop

            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning("[%d/%d] Attempt %d/%d FAILED: %s — retrying...",
                                   i + 1, len(anomalies), attempt, max_retries, e)
                else:
                    logger.error("[%d/%d] All %d attempts FAILED: %s",
                                 i + 1, len(anomalies), max_retries, e)

        # ── Ket qua sau retry ─────────────────────────────────────────────────
        if last_error is None and output is not None:
            # Thanh cong
            consecutive_failures = 0
            results.append({
                "index":       i + 1,
                "resource_id": record["resource_id"],
                "environment": record["environment"],
                "root_cause":  rca.get("root_cause_category"),
                "risk_level":  rca.get("risk_level"),
                "action":      output["engineering_dashboard_data"]["mitigation_action"]["immediate_action"],
                "audit_id":    output["anomaly_metadata"].get("audit_id"),
                "status":      "ok",
                "attempts":    attempt,
            })
            success += 1
        else:
            # That bai sau max_retries lan
            consecutive_failures += 1
            errors += 1
            results.append({
                "index":       i + 1,
                "resource_id": record.get("resource_id", "unknown"),
                "environment": record.get("environment", "unknown"),
                "status":      "error",
                "error":       str(last_error),
                "attempts":    max_retries,
            })
            logger.error("[%d/%d] SKIPPED after %d retries: %s",
                         i + 1, len(anomalies), max_retries, last_error)

            # Dung chuong trinh neu consecutive_failures >= max_retries
            if stop_on_fail and consecutive_failures >= max_retries:
                logger.critical(
                    "STOPPING PIPELINE: %d consecutive failures detected. "
                    "Check Bedrock/DynamoDB connection.",
                    consecutive_failures,
                )
                logger.critical("Run: python bedrock_test.py  to diagnose")
                break

    logger.info("=" * 65)
    logger.info("PHASE 2 COMPLETE: %d success, %d errors", success, errors)
    return results


# =============================================================================
# Print summary
# =============================================================================
def print_summary(results: list):
    print("\n" + "=" * 65)
    print("PIPELINE SUMMARY")
    print("=" * 65)

    action_counts = {}
    env_counts    = {}

    for r in results:
        if r.get("status") == "ok":
            a = r.get("action", "unknown")
            e = r.get("environment", "unknown")
            action_counts[a] = action_counts.get(a, 0) + 1
            env_counts[e]    = env_counts.get(e, 0) + 1

    print(f"Total anomalies processed : {len(results)}")
    print(f"Success                   : {sum(1 for r in results if r.get('status') == 'ok')}")
    print(f"Errors                    : {sum(1 for r in results if r.get('status') == 'error')}")
    print("\nActions taken:")
    for action, count in sorted(action_counts.items(), key=lambda x: -x[1]):
        print(f"  {action:<30} : {count}")
    print("\nBy environment:")
    for env, count in sorted(env_counts.items(), key=lambda x: -x[1]):
        print(f"  {env:<25} : {count}")
    print("\nAudit trail:")
    for r in results:
        if r.get("status") == "ok":
            print(f"  [{r['index']:3d}] {r['environment']:<15} {r['root_cause']:<20} "
                  f"{r['action']:<25} audit={r.get('audit_id', 'N/A')[:8]}...")
    print("=" * 65)
    print("Audit records stored in DynamoDB: FinOps_Audit_Store")
    print("Retention: 90 days (TTL auto-delete)")
    print("=" * 65)


# =============================================================================
# Main
# =============================================================================
def main():
    args = parse_args()

    # Config moi truong
    dry_run = not args.live
    use_mock = not args.no_mock

    os.environ["BEDROCK_MOCK"]   = "false" if args.no_mock else "true"
    os.environ["FINOPS_DRY_RUN"] = "false" if args.live else "true"

    slack_url = args.slack or os.environ.get("SLACK_WEBHOOK_URL")

    logger.info("Pipeline config: dry_run=%s | bedrock_mock=%s | limit=%s | env_filter=%s",
                dry_run, use_mock, args.limit, args.env)

    if not dry_run and not use_mock:
        logger.warning("LIVE MODE: Real AWS API calls will be made!")
        logger.warning("Affected environments: dev, ml-research, data-analytics (auto-stop)")
        logger.warning("Staging: 4h time-lock will be activated")
        logger.warning("Prod: tag-for-review only (no auto-stop)")

    # Phase 1: Detection
    evaluation_df, df_test_raw = run_detection_pipeline()

    # Phase 2: RCA + Action
    results = run_rca_action_pipeline(
        evaluation_df = evaluation_df,
        df_test_raw   = df_test_raw,
        dry_run       = dry_run,
        limit         = args.limit,
        filter_env    = args.env,
        slack_webhook = slack_url,
    )

    # Summary
    print_summary(results)

    # Save full results to JSON
    out_file = "pipeline_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info("Full results saved to: %s", out_file)


if __name__ == "__main__":
    main()