import os
import sys
import json
import pickle
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import List, Optional, Dict, Any

from engine.strategies.base import DetectionStrategy
from models.domain import AnomalyResult, CostRecord
from models.enums import AnomalyType
from config.settings import get_settings

logger = logging.getLogger("finops-engine.strategy.xgboost")

# Try importing xgboost
try:
    import xgboost as xgb
except ImportError:
    logger.error("xgboost library is not installed in the runtime environment.")

class XGBoostStrategy(DetectionStrategy):
    """
    ML-based detection using per-service XGBoost models.
    Loads models and scalers from engine/ml_artifacts/.
    Implements rule-based guardrail and traffic-based post-filtering.
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.artifacts_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            "ml_artifacts"
        )
        
        self.models: Dict[str, Any] = {}
        self.scalers: Dict[str, Any] = {}
        self.feature_specs: Dict[str, List[str]] = {}
        
        # History store to compute rolling features: resource_id -> list of hourly records
        # Keep up to 14 days (336 hours) of history
        self.history = defaultdict(list)
        
        self._load_artifacts()
        
    @property
    def strategy_name(self) -> str:
        return "xgboost_v1"
        
    def _load_artifacts(self):
        """Load XGBoost models, scalers, and feature specifications from disk."""
        logger.info(f"Loading ML artifacts from: {self.artifacts_dir}")
        resource_types = ["compute", "database", "gpu_compute", "container", "cache", "storage"]
        
        for rtype in resource_types:
            model_path = os.path.join(self.artifacts_dir, f"best_model_{rtype}.json")
            scaler_path = os.path.join(self.artifacts_dir, f"scaler_{rtype}.pkl")
            spec_path = os.path.join(self.artifacts_dir, f"features_spec_{rtype}.txt")
            
            if os.path.exists(model_path) and os.path.exists(scaler_path) and os.path.exists(spec_path):
                try:
                    # Load model
                    model = xgb.XGBClassifier()
                    model.load_model(model_path)
                    self.models[rtype] = model
                    
                    # Load scaler
                    with open(scaler_path, "rb") as f:
                        scaler = pickle.load(f)
                    self.scalers[rtype] = scaler
                    
                    # Load feature spec
                    with open(spec_path, "r") as f:
                        spec = [line.strip() for line in f.read().splitlines() if line.strip()]
                    self.feature_specs[rtype] = spec
                    
                    logger.info(f"Successfully loaded artifacts for resource type: {rtype}")
                except Exception as e:
                    logger.error(f"Error loading artifacts for {rtype}: {str(e)}")
            else:
                logger.warning(f"Missing artifacts for {rtype}. Looked in paths:\n - {model_path}\n - {scaler_path}\n - {spec_path}")

    def _classify_resource_type(self, usage_type: str, service: str) -> str:
        """Classify resource type based on CUR usage_type and service."""
        usage_type_lower = usage_type.lower()
        service_lower = service.lower()
        
        # 1. GPU Compute
        if "AmazonEC2" in service or "ec2" in service_lower:
            if any(kw in usage_type_lower for kw in ["p3.", "p4.", "g4.", "g5.", "ml.", "gpu"]):
                return "gpu_compute"
            
        # 2. Container
        if any(kw in service_lower for kw in ["eks", "ecs", "container"]):
            return "container"
            
        # 3. Database / Cache
        if "rds" in service_lower or "database" in service_lower:
            return "database"
        if "elasticache" in service_lower or "cache" in service_lower:
            return "cache"
            
        # 4. Storage
        if "s3" in service_lower or "storage" in service_lower:
            return "storage"
            
        # 5. Default Compute
        if "AmazonEC2" in service or "ec2" in service_lower:
            return "compute"
            
        return "compute" # Fallback to compute

    def detect(
        self,
        cost_window: List[CostRecord],
        baseline: Optional[object],
        tenant_id: str,
        utilization_metrics: Optional[List[Any]] = None,
        business_context: Optional[Any] = None,
    ) -> AnomalyResult:
        
        if not cost_window:
            return AnomalyResult(
                is_anomaly=False,
                anomaly_type=AnomalyType.OTHER,
                severity=0.0,
                confidence=1.0,
                reasoning="Empty cost window, no data to detect."
            )
            
        # Group cost records by resource_id
        # In Contract v1.5.0, CDO sends daily unblended cost for each resource.
        # We can extract the representative resource ID.
        # Let's find the resource with the maximum cost for this period.
        top_cost_record = max(cost_window, key=lambda x: x.cost_usd)
        resource_id = top_cost_record.idempotency_key or "unknown_resource"
        
        # Find corresponding utilization metric
        metric_match = None
        if utilization_metrics:
            for metric in utilization_metrics:
                # CDO passes resource_id in utilization metric
                if hasattr(metric, "resource_id") and metric.resource_id == resource_id:
                    metric_match = metric
                    break
        
        # Determine resource type
        rtype = self._classify_resource_type(top_cost_record.usage_type, top_cost_record.service)
        logger.info(f"Detecting anomaly for resource: {resource_id} (Type: {rtype})")
        
        if rtype not in self.models:
            logger.warning(f"No ML model available for resource type: {rtype}. Falling back to default baseline.")
            # Fallback to Statistical/Rule-based heuristic if model not loaded
            return self._fallback_heuristic(cost_window, baseline, tenant_id)
            
        # -------------------------------------------------------------
        # 1. Reconstruct 24 hourly rows from daily CUR & utilization payload
        # -------------------------------------------------------------
        daily_cost = sum(r.cost_usd for r in cost_window)
        hourly_cost = daily_cost / 24.0
        
        cpu_hourly = [0.0] * 24
        if metric_match and hasattr(metric_match, "cpu_utilization_hourly") and metric_match.cpu_utilization_hourly:
            cpu_hourly = metric_match.cpu_utilization_hourly
        elif metric_match and hasattr(metric_match, "cpu_percent") and metric_match.cpu_percent is not None:
            cpu_hourly = [metric_match.cpu_percent] * 24
            
        # Other daily metrics
        mem = getattr(metric_match, "memory_mib", 0.0) or 0.0
        net_in = getattr(metric_match, "network_in_bytes", 0.0) or 0.0
        net_out = getattr(metric_match, "network_out_bytes", 0.0) or 0.0
        disk_io = getattr(metric_match, "disk_io_ops", 0.0) or 0.0
        db_conn = getattr(metric_match, "database_connections", 0.0) or 0.0
        gpu_util = getattr(metric_match, "gpu_utilization", 0.0) or 0.0
        
        # Parse timestamp
        start_date = top_cost_record.cost_period_start
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
            
        current_day_rows = []
        for h in range(24):
            dt = start_date + timedelta(hours=h)
            row = {
                "timestamp": dt,
                "cost": hourly_cost,
                "cpu_percent": cpu_hourly[h],
                "memory_mib": mem,
                "network_in_bytes": net_in / 24.0,
                "network_out_bytes": net_out / 24.0,
                "disk_io_ops": disk_io / 24.0,
                "database_connections": db_conn,
                "gpu_utilization": gpu_util,
                "cpu_utilization_hourly": json.dumps(cpu_hourly)
            }
            current_day_rows.append(row)
            
        # -------------------------------------------------------------
        # 2. Append to history & keep max 14 days (336 hours)
        # -------------------------------------------------------------
        res_history = self.history[resource_id]
        
        # If history is empty, initialize it with previous days of normal baseline to prevent cold-start NaNs
        if not res_history:
            # Backfill with 7 days of normal daily cost/metrics based on current values
            for d in range(7, 0, -1):
                prev_date = start_date - timedelta(days=d)
                for h in range(24):
                    row = {
                        "timestamp": prev_date + timedelta(hours=h),
                        "cost": hourly_cost,
                        "cpu_percent": cpu_hourly[h],
                        "memory_mib": mem,
                        "network_in_bytes": net_in / 24.0,
                        "network_out_bytes": net_out / 24.0,
                        "disk_io_ops": disk_io / 24.0,
                        "database_connections": db_conn,
                        "gpu_utilization": gpu_util,
                        "cpu_utilization_hourly": json.dumps(cpu_hourly)
                    }
                    res_history.append(row)
                    
        # Append current day's hourly rows
        res_history.extend(current_day_rows)
        
        # Keep only the last 14 days
        max_history_len = 336
        if len(res_history) > max_history_len:
            res_history = res_history[-max_history_len:]
        self.history[resource_id] = res_history
        
        # Convert history to DataFrame to compute features
        df_hist = pd.DataFrame(res_history)
        df_hist = df_hist.sort_values(by="timestamp").reset_index(drop=True)
        
        # -------------------------------------------------------------
        # 3. Compute raw features
        # -------------------------------------------------------------
        # Time features
        df_hist["hour_of_day"] = df_hist["timestamp"].dt.hour
        df_hist["day_of_week"] = df_hist["timestamp"].dt.dayofweek
        df_hist["is_weekend"] = df_hist["day_of_week"].apply(lambda x: 1 if x >= 5 else 0)
        df_hist["is_business_hours"] = ((df_hist["hour_of_day"] >= 9) & (df_hist["hour_of_day"] <= 17) & (df_hist["day_of_week"] < 5)).astype(int)
        
        metrics_cols = ["cost", "cpu_percent", "memory_mib", "network_in_bytes", "network_out_bytes", "disk_io_ops", "database_connections", "gpu_utilization"]
        for col in metrics_cols:
            df_hist[f"{col}_lag_1h"] = df_hist[col].shift(1)
            df_hist[f"{col}_lag_1d"] = df_hist[col].shift(24)
            df_hist[f"{col}_lag_2d"] = df_hist[col].shift(48)
            df_hist[f"{col}_lag_7d"] = df_hist[col].shift(168)
            df_hist[f"{col}_diff_1h"] = df_hist[col] - df_hist[f"{col}_lag_1h"]
            df_hist[f"{col}_diff_1d"] = df_hist[col] - df_hist[f"{col}_lag_1d"]
            
            # Rolling 7d (168h)
            df_hist[f"{col}_roll_median_7d"] = df_hist[col].rolling(window=168, min_periods=1).median()
            diff_7d = (df_hist[col] - df_hist[f"{col}_roll_median_7d"]).abs()
            df_hist[f"{col}_roll_mad_7d"] = diff_7d.rolling(window=168, min_periods=1).median().replace(0, 0.001).fillna(0.001)
            df_hist[f"{col}_deviation_ratio_7d"] = (df_hist[col] - df_hist[f"{col}_roll_median_7d"]) / df_hist[f"{col}_roll_mad_7d"]
            
            # Rolling 14d (336h)
            df_hist[f"{col}_roll_median_14d"] = df_hist[col].rolling(window=336, min_periods=1).median()
            diff_14d = (df_hist[col] - df_hist[f"{col}_roll_median_14d"]).abs()
            df_hist[f"{col}_roll_mad_14d"] = diff_14d.rolling(window=336, min_periods=1).median().replace(0, 0.001).fillna(0.001)
            df_hist[f"{col}_deviation_ratio_14d"] = (df_hist[col] - df_hist[f"{col}_roll_median_14d"]) / df_hist[f"{col}_roll_mad_14d"]
            
        df_hist['network_ratio'] = df_hist['network_in_bytes'] / (df_hist['network_out_bytes'] + 1e-5)
        df_hist['cpu_per_dollar'] = df_hist['cpu_percent'] / (df_hist['cost'] + 1e-5)
        df_hist['gpu_per_dollar'] = df_hist['gpu_utilization'] / (df_hist['cost'] + 1e-5)
        df_hist['conn_per_dollar'] = df_hist['database_connections'] / (df_hist['cost'] + 1e-5)
        
        # CPU hourly std
        def get_std_safe(val_str):
            try:
                arr = json.loads(val_str)
                return float(np.std(arr))
            except:
                return 0.0
        df_hist['cpu_hourly_std'] = df_hist['cpu_utilization_hourly'].apply(get_std_safe)
        
        # Backfill NaNs
        cols_to_fill = [c for c in df_hist.columns if c not in ["timestamp", "cpu_utilization_hourly"]]
        for col in cols_to_fill:
            df_hist[col] = df_hist[col].bfill().fillna(0)
            
        # Get only the current day's rows (last 24 rows)
        df_current = df_hist.iloc[-24:].reset_index(drop=True)
        
        # -------------------------------------------------------------
        # 4. Scale and Predict using the corresponding model
        # -------------------------------------------------------------
        model = self.models[rtype]
        scaler = self.scalers[rtype]
        spec_features = self.feature_specs[rtype]
        
        X = df_current[spec_features].copy()
        
        # Scale continuous features
        categorical_cols = ["hour_of_day", "day_of_week", "is_weekend", "is_business_hours"]
        cols_to_scale = [c for c in spec_features if c not in categorical_cols]
        if cols_to_scale:
            X[cols_to_scale] = scaler.transform(X[cols_to_scale])
            
        # Model predictions (probabilities for confidence)
        preds = model.predict(X)
        probs = model.predict_proba(X)[:, 1] # Probability of anomaly (class 1)
        
        # Determine if any hour in the current day was anomalous
        anom_hours = np.where(preds == 1)[0]
        is_anomaly = len(anom_hours) > 0
        
        max_prob = float(np.max(probs))
        
        # Get baseline daily cost for comparison
        if len(df_hist) > 24:
            history_rows = df_hist.iloc[:-24]
            baseline_avg = float(history_rows["cost"].sum()) * 24.0 / len(history_rows)
        else:
            baseline_avg = daily_cost
        cost_delta = daily_cost - baseline_avg
        cost_delta_pct = ((daily_cost / max(0.01, baseline_avg)) - 1) * 100
        
        # Prepare anomaly results
        severity_score = 0.0
        confidence_score = max_prob
        reasoning = f"[ML-XGBoost] Resource {resource_id} is operating normally. Max anomaly prob: {max_prob:.2%}"
        anomaly_type = AnomalyType.OTHER
        
        if is_anomaly:
            # Anomaly detected! Let's classify the anomaly type based on metrics
            # Heuristics for classifying anomalous window
            anomaly_type = self._classify_anomaly_type(df_current, cost_delta_pct)
            
            # Severity is mapped from confidence and cost delta
            severity_score = min(1.0, max(0.1, (daily_cost / max(0.01, baseline_avg) - 1.0) / 3.0))
            reasoning = (
                f"[ML-XGBoost] Cost anomaly detected on {rtype} resource: "
                f"${daily_cost:.2f} vs baseline ${baseline_avg:.2f}/day "
                f"({cost_delta_pct:+.1f}%). Conf: {max_prob:.2%}."
            )
            
        # -------------------------------------------------------------
        # 5. Hybrid Heuristics Guardrail (Rule-based cost spike check)
        # -------------------------------------------------------------
        cost_ratio = daily_cost / max(0.01, baseline_avg)
        is_guardrail_triggered = cost_ratio >= self.settings.cost_spike_multiplier
        
        if is_guardrail_triggered:
            is_anomaly = True
            anomaly_type = AnomalyType.SUDDEN_SPIKE if anomaly_type == AnomalyType.OTHER else anomaly_type
            severity_score = max(severity_score, 0.85)
            confidence_score = max(confidence_score, 0.95)
            reasoning = (
                f"[GUARDRAIL TRIGGERED] Cost spike detected: ${daily_cost:.2f} vs baseline ${baseline_avg:.2f}/day "
                f"({cost_ratio:.1f}x). Threshold: {self.settings.cost_spike_multiplier}x."
            )
            
        # -------------------------------------------------------------
        # 6. Post-Filter Verification (False Positive Reduction)
        # -------------------------------------------------------------
        if is_anomaly and business_context and hasattr(business_context, "traffic_volume") and business_context.traffic_volume > 0:
            traffic = business_context.traffic_volume
            cost_per_req = daily_cost / traffic
            
            # Get historical average cost per request
            hist_traffic_volume = traffic # fallback
            hist_avg_cost_per_req = baseline_avg / hist_traffic_volume
            
            # If cost per request is stable (under 1.2x of historical average), it's a benign scaling event
            cost_per_req_ratio = cost_per_req / max(1e-6, hist_avg_cost_per_req)
            
            if cost_per_req_ratio < 1.2 and not is_guardrail_triggered:
                # Cancel the alert as it's a false positive caused by normal business growth!
                is_anomaly = False
                anomaly_type = AnomalyType.OTHER
                severity_score = 0.05
                confidence_score = 0.90
                reasoning = (
                    f"[POST-FILTER] Anomaly warning suppressed. Cost spike is justified by traffic growth: "
                    f"Cost/Req ratio is {cost_per_req_ratio:.2f}x (stable under 1.20x)."
                )
                logger.info(f"Post-filter suppressed false alarm for resource {resource_id}.")
                
        return AnomalyResult(
            is_anomaly=is_anomaly,
            anomaly_type=anomaly_type,
            severity=round(severity_score, 2),
            confidence=round(confidence_score, 2),
            reasoning=reasoning[:300],
            affected_account=top_cost_record.account_id,
            affected_service=top_cost_record.service,
            affected_resource_id=resource_id,
            baseline_cost_usd=round(baseline_avg, 2),
            current_cost_usd=round(daily_cost, 2),
            cost_delta_usd=round(cost_delta, 2),
            cost_delta_pct=round(cost_delta_pct, 1),
        )

    def _fallback_heuristic(self, cost_window: List[CostRecord], baseline: Optional[object], tenant_id: str) -> AnomalyResult:
        """Statistical fallback strategy when ML model is unavailable."""
        daily_cost = sum(r.cost_usd for r in cost_window)
        baseline_avg = 50.0 # dummy default baseline
        ratio = daily_cost / baseline_avg
        
        is_spike = ratio >= self.settings.cost_spike_multiplier
        top_item = cost_window[0]
        
        return AnomalyResult(
            is_anomaly=is_spike,
            anomaly_type=AnomalyType.SUDDEN_SPIKE if is_spike else AnomalyType.OTHER,
            severity=0.85 if is_spike else 0.05,
            confidence=0.90,
            reasoning=f"[FALLBACK] Cost ${daily_cost:.2f} vs baseline ${baseline_avg:.2f}/day ({ratio:.1f}x).",
            affected_account=top_item.account_id,
            affected_service=top_item.service,
            affected_resource_id=top_item.idempotency_key,
            baseline_cost_usd=baseline_avg,
            current_cost_usd=daily_cost,
            cost_delta_usd=daily_cost - baseline_avg,
            cost_delta_pct=(ratio - 1) * 100,
        )

    def _classify_anomaly_type(self, df_current: pd.DataFrame, cost_delta_pct: float) -> AnomalyType:
        """Classify the anomaly type based on metrics in the current anomalous window."""
        # 1. runaway_usage: high GPU usage
        if "gpu_utilization" in df_current.columns and df_current["gpu_utilization"].max() > 80.0:
            return AnomalyType.RUNAWAY_USAGE
            
        # 2. database connections / saturation
        if "database_connections" in df_current.columns and df_current["database_connections"].max() > 200:
            return AnomalyType.RUNAWAY_USAGE
            
        # 3. idle_resource: CPU is very low but cost is still generated
        if "cpu_percent" in df_current.columns and df_current["cpu_percent"].mean() < 3.0:
            return AnomalyType.IDLE_RESOURCE
            
        # 4. sudden_spike: cost jumps significantly
        if cost_delta_pct >= 200.0:
            return AnomalyType.SUDDEN_SPIKE
            
        return AnomalyType.SUDDEN_SPIKE
