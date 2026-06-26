import os
import pickle
import pandas as pd
import numpy as np
import xgboost as xgb
from datetime import datetime

# Add current folder to path
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from FEATURE_v2 import load_and_merge, transform_features, FEATURE_COLS_V2

def normalize_resource_id(res_id):
    if not isinstance(res_id, str):
        return res_id
    if res_id.startswith("arn:aws:"):
        parts = res_id.split(":")
        last_part = parts[-1]
        if "/" in last_part:
            return last_part.split("/")[-1]
        return last_part
    return res_id

class AnomalyDetectorService:
    def __init__(self, serving_dir=None, data_dir=None):
        if data_dir is None:
            data_dir = r"d:\Xbrain\Capstone-AIOps-02\data"
        if serving_dir is None:
            serving_dir = os.path.join(data_dir, "serving_models")
            
        self.data_dir = data_dir
        self.serving_dir = serving_dir
        
        # 1. Load model
        model_path = os.path.join(serving_dir, "xgboost_anomaly_detector.json")
        self.model = xgb.XGBClassifier()
        self.model.load_model(model_path)
        
        # 2. Load optimal threshold
        threshold_path = os.path.join(serving_dir, "optimal_threshold.txt")
        with open(threshold_path, "r") as f:
            self.optimal_threshold = float(f.read().strip())
            
        # 3. Load features list
        features_path = os.path.join(serving_dir, "features_list.txt")
        with open(features_path, "r") as f:
            self.features_list = f.read().strip().split(",")
            
        # 4. Load training stats (for imputation)
        stats_path = os.path.join(serving_dir, "train_stats.pkl")
        with open(stats_path, "rb") as f:
            self.train_stats = pickle.load(f)
            
        # 5. Load historical merged data (CUR + Metrics) as lookback cache
        print("Loading historical merged database...")
        self.df_history = load_and_merge(data_dir)
        self.df_history['line_item_resource_id'] = self.df_history['line_item_resource_id'].apply(normalize_resource_id)
        
        # Load raw metrics store for merging new incoming records
        self.df_metrics_store = self._load_metrics_store()
        
        print(f"AnomalyDetectorService initialized with {len(self.df_history)} history rows. Threshold: {self.optimal_threshold}")
        
    def _load_metrics_store(self):
        from FEATURE_v2 import METRICS_MAPPING, _max_idle_streak
        cpu_cols = [f'cpu_h{i}' for i in range(24)]
        metrics_list = []

        for service, filename in METRICS_MAPPING.items():
            fpath = os.path.join(self.data_dir, filename)
            if not os.path.exists(fpath):
                continue
            df = pd.read_csv(fpath)
            df['derived_service_code'] = service
            df['clean_date'] = pd.to_datetime(df['timestamp']).dt.normalize()

            if set(cpu_cols).issubset(df.columns):
                cpu_matrix  = df[cpu_cols].fillna(0).values
                idle_matrix = cpu_matrix < 5
                df['idle_hours_continuous'] = np.apply_along_axis(
                    _max_idle_streak, 1, idle_matrix
                )
                df['cpu_mean'] = cpu_matrix.mean(axis=1)
                df['cpu_std']  = cpu_matrix.std(axis=1)
                df['cpu_max']  = cpu_matrix.max(axis=1)
                df['cpu_min']  = cpu_matrix.min(axis=1)
            else:
                df['idle_hours_continuous'] = 0
                df['cpu_mean'] = df['cpu_std'] = df['cpu_max'] = df['cpu_min'] = np.nan

            metrics_list.append(df)

        df_metrics = pd.concat(metrics_list, ignore_index=True)
        df_metrics = df_metrics.drop_duplicates(subset=['resource_id', 'clean_date'])
        df_metrics['resource_id'] = df_metrics['resource_id'].apply(normalize_resource_id)
        return df_metrics

    def detect_anomalies(self, cur_items: list, business_contexts: list = None, telemetry_delay_event: bool = False, current_ce_cost_gap_usd: float = 0.0):
        if not cur_items:
            return []
            
        # Parse business context mapping (by account)
        contexts_by_account = {}
        if business_contexts:
            for ctx in business_contexts:
                acc_id = ctx.get('linked_account_id')
                contexts_by_account[acc_id] = ctx
                
        # Convert incoming payload to DataFrame
        df_batch_raw = pd.DataFrame(cur_items)
        df_batch_raw['line_item_resource_id'] = df_batch_raw['line_item_resource_id'].apply(normalize_resource_id)
        df_batch_raw['clean_date'] = pd.to_datetime(df_batch_raw['line_item_usage_start_date']).dt.normalize()
        
        # Ensure correct numeric types
        df_batch_raw['line_item_unblended_cost'] = pd.to_numeric(df_batch_raw['line_item_unblended_cost'], errors='coerce').fillna(0.0)
        df_batch_raw['line_item_usage_amount'] = pd.to_numeric(df_batch_raw['line_item_usage_amount'], errors='coerce').fillna(0.0)
        
        # Group by resource-day
        agg_dict = {
            'line_item_unblended_cost': ('line_item_unblended_cost', 'sum'),
            'line_item_usage_amount': ('line_item_usage_amount', 'sum'),
            'resource_tags_user_team': ('resource_tags_user_team', 'first'),
            'resource_tags_user_owner': ('resource_tags_user_owner', 'first')
        }
        if 'is_estimated' in df_batch_raw.columns:
            agg_dict['is_estimated'] = ('is_estimated', 'first')
            
        df_batch_grouped = df_batch_raw.groupby([
            'line_item_resource_id', 'line_item_product_code', 
            'line_item_usage_account_id', 'line_item_usage_account_name', 'clean_date'
        ]).agg(**agg_dict).reset_index()
        
        if 'is_estimated' not in df_batch_grouped.columns:
            df_batch_grouped['is_estimated'] = False
            
        # Merge batch with metrics store (left join so resources without metrics can be imputed)
        df_batch_merged = pd.merge(
            df_batch_grouped, self.df_metrics_store,
            left_on=['line_item_resource_id', 'clean_date'],
            right_on=['resource_id', 'clean_date'],
            how='left'
        )
        
        # Date of the batch
        batch_date = df_batch_merged['clean_date'].max()
        
        # Append incoming day to historical lookback cache
        # To avoid duplicating memory, we only keep history up to 60 days lookback
        cutoff_date = batch_date - pd.Timedelta(days=60)
        df_hist_sub = self.df_history[self.df_history['clean_date'] >= cutoff_date].copy()
        
        # Concatenate history and batch
        df_combined = pd.concat([df_hist_sub, df_batch_merged], ignore_index=True)
        df_combined = df_combined.drop_duplicates(subset=['line_item_resource_id', 'clean_date'], keep='last')
        df_combined = df_combined.sort_values(['line_item_resource_id', 'clean_date']).reset_index(drop=True)
        
        # Run transform features using stats
        df_transformed = transform_features(df_combined, self.train_stats)
        
        # Extract rows matching (resource_id, date) keys in the incoming batch
        batch_keys = df_batch_merged[['line_item_resource_id', 'clean_date']].drop_duplicates()
        df_batch_features = pd.merge(
            df_transformed, batch_keys,
            left_on=['line_item_resource_id', 'clean_date'],
            right_on=['line_item_resource_id', 'clean_date'],
            how='inner'
        )
        if df_batch_features.empty:
            return []
            
        X_batch = df_batch_features[self.features_list].values
        
        # Predict probabilities for 3 classes: [Normal, Anomaly, Benign]
        prob_all = self.model.predict_proba(X_batch)
        prob_anomaly = prob_all[:, 1] # Anomaly is class 1
        
        print("DEBUG PREDICTIONS:")
        for idx, row in df_batch_features.reset_index(drop=True).iterrows():
            print(f"  Resource: {row['line_item_resource_id']} | Cost: {row['line_item_unblended_cost']} | Prob Anomaly: {prob_anomaly[idx]:.4f} | Optimal Threshold: {self.optimal_threshold:.4f}")
            
        anomalies_detected = []
        for idx, row in df_batch_features.reset_index(drop=True).iterrows():
            res_id = row['line_item_resource_id']
            prod_code = row['line_item_product_code']
            acc_id = row['line_item_usage_account_id']
            acc_name = row['line_item_usage_account_name']
            cost = row['line_item_unblended_cost']
            usage = row['line_item_usage_amount']
            is_est = row['is_estimated']
            prob = float(prob_anomaly[idx])
            
            # Fetch business context for override logic
            ctx = contexts_by_account.get(acc_id, {})
            traffic_vol = float(ctx.get('traffic_volume', 0.0))
            campaign_flag = bool(ctx.get('campaign_flag', False))
            load_test_flag = bool(ctx.get('load_test_flag', False))
            
            anomaly_detected = False
            confidence = prob
            action_strategy = "Auto-Containment"
            immediate_action = "auto-shutdown"
            
            # 1. Base threshold check
            if prob >= self.optimal_threshold:
                anomaly_detected = True
                
            # 2. Campaign surge / load test overrides (benign confounders)
            if anomaly_detected:
                if campaign_flag or load_test_flag:
                    anomaly_detected = False
                    action_strategy = "Alert-only (Benign Surge)"
                    immediate_action = "tag-for-review"
                    confidence = 0.10
                    
            # 3. Telemetry Delay check
            if anomaly_detected and telemetry_delay_event:
                confidence = min(confidence, 0.45)
                action_strategy = "Manual Review Required (Telemetry Delay)"
                immediate_action = "tag-for-review"
                
            # 4. Estimated cost check
            if anomaly_detected and is_est:
                confidence = min(confidence, 0.49)
                action_strategy = "Manual Review Required (Estimated Data)"
                immediate_action = "tag-for-review"
                
            # Find missing tags
            missing_tags = []
            if row['team_missing'] == 1:
                missing_tags.append('resource_tags_user_team')
            if row['owner_missing'] == 1:
                missing_tags.append('resource_tags_user_owner')
                
            if anomaly_detected or prob >= self.optimal_threshold:
                anomalies_detected.append({
                    'resource_id': res_id,
                    'product_code': prod_code,
                    'account_id': acc_id,
                    'account_name': acc_name,
                    'unblended_cost_24h': cost,
                    'usage_amount_24h': usage,
                    'confidence_score': confidence,
                    'is_anomaly': anomaly_detected,
                    'action_strategy': action_strategy,
                    'immediate_action': immediate_action,
                    'missing_tags': missing_tags
                })
                
        # Update our in-memory history with today's batch to keep lookback fresh
        self.df_history = pd.concat([self.df_history, df_batch_merged], ignore_index=True)
        self.df_history = self.df_history.drop_duplicates(subset=['line_item_resource_id', 'clean_date'], keep='last')
        
        return anomalies_detected
