import os
import pickle
import pandas as pd
import numpy as np
import xgboost as xgb

class FeatureStore:
    """
    Simulates a DynamoDB Feature Store.
    It pre-loads historical data from cur_line_items.csv to provide the 28-day lookback window.
    """
    def __init__(self, cur_path):
        print("Initializing simulated Feature Store...")
        df_raw = pd.read_csv(cur_path)
        df_raw['date'] = pd.to_datetime(df_raw['line_item_usage_start_date']).dt.date
        
        # We group to resource-day level to store histories
        self.history = df_raw.groupby(['line_item_resource_id', 'date']).agg(
            cost=('line_item_unblended_cost', 'sum'),
            usage=('line_item_usage_amount', 'sum')
        ).reset_index()
        self.history = self.history.sort_values(by=['line_item_resource_id', 'date']).reset_index(drop=True)
        
    def get_lookback_data(self, resource_id, current_date, window_days=28):
        """
        Returns cost and usage lists for the last `window_days` before `current_date`.
        """
        sub = self.history[
            (self.history['line_item_resource_id'] == resource_id) & 
            (self.history['date'] < current_date)
        ].tail(window_days)
        
        costs = sub['cost'].tolist()
        usages = sub['usage'].tolist()
        dates = sub['date'].tolist()
        return costs, usages, dates

    def update_store(self, resource_id, date, cost, usage):
        """
        Updates the history store with today's metrics.
        """
        # Append new row
        new_row = pd.DataFrame([{
            'line_item_resource_id': resource_id,
            'date': date,
            'cost': cost,
            'usage': usage
        }])
        self.history = pd.concat([self.history, new_row], ignore_index=True)
        self.history = self.history.drop_duplicates(subset=['line_item_resource_id', 'date'], keep='last')


class AnomalyDetectorService:
    def __init__(self, serving_dir=r"d:\Xbrain\Capstone-AIOps-02\data\serving_models", cur_path=r"d:\Xbrain\Capstone-AIOps-02\data\cur_line_items.csv"):
        self.serving_dir = serving_dir
        
        # Load scaler
        scaler_path = os.path.join(serving_dir, "robust_scaler.pkl")
        with open(scaler_path, "rb") as f:
            self.scaler = pickle.load(f)
            
        # Load XGBoost model
        self.model = xgb.XGBClassifier()
        self.model.load_model(os.path.join(serving_dir, "xgboost_anomaly_detector.json"))
        
        # Load optimal threshold
        threshold_path = os.path.join(serving_dir, "optimal_threshold.txt")
        with open(threshold_path, "r") as f:
            self.optimal_threshold = float(f.read().strip())
            
        # Load features list
        features_path = os.path.join(serving_dir, "features_list.txt")
        with open(features_path, "r") as f:
            self.features_list = f.read().strip().split(",")
            
        # Initialize feature store
        self.feature_store = FeatureStore(cur_path)
        print(f"AnomalyDetectorService successfully loaded. Threshold: {self.optimal_threshold}")

    def detect_anomalies(self, cur_items: list, business_contexts: list = None, telemetry_delay_event: bool = False, current_ce_cost_gap_usd: float = 0.0):
        """
        Executes sync anomaly detection on a batch payload.
        cur_items: list of line items (matching cur_line_items.csv schema)
        business_contexts: list of business context parameters (from request header/body)
        """
        # 1. Parse business context mapping (by account)
        contexts_by_account = {}
        if business_contexts:
            for ctx in business_contexts:
                acc_id = ctx.get('linked_account_id')
                contexts_by_account[acc_id] = ctx

        # Group current items by resource_id to run resource-day prediction
        df_current = pd.DataFrame(cur_items)
        if df_current.empty:
            return []
            
        # Ensure correct types
        df_current['line_item_unblended_cost'] = pd.to_numeric(df_current['line_item_unblended_cost'], errors='coerce').fillna(0.0)
        df_current['line_item_usage_amount'] = pd.to_numeric(df_current['line_item_usage_amount'], errors='coerce').fillna(0.0)
        
        # Group by resource-day
        agg_dict = {
            'cost': ('line_item_unblended_cost', 'sum'),
            'usage': ('line_item_usage_amount', 'sum'),
            'team_tag': ('resource_tags_user_team', 'first'),
            'owner_tag': ('resource_tags_user_owner', 'first')
        }
        if 'is_estimated' in df_current.columns:
            agg_dict['is_estimated'] = ('is_estimated', 'first')

        grouped = df_current.groupby([
            'line_item_resource_id', 'line_item_product_code', 
            'line_item_usage_account_id', 'line_item_usage_account_name'
        ]).agg(**agg_dict).reset_index()
        
        if 'is_estimated' not in grouped.columns:
            grouped['is_estimated'] = False

        # Date of batch (take the max date in current payload)
        if 'line_item_usage_start_date' in df_current.columns:
            batch_date = pd.to_datetime(df_current['line_item_usage_start_date']).max().date()
        else:
            batch_date = pd.to_datetime("2026-05-31").date() # default fallback for test

        # Calculate peer median for today's batch to compute peer_ratio
        peer_medians = grouped.groupby(['line_item_product_code', 'line_item_usage_account_name'])['cost'].median().to_dict()

        anomalies_detected = []

        for _, row in grouped.iterrows():
            res_id = row['line_item_resource_id']
            prod_code = row['line_item_product_code']
            acc_id = row['line_item_usage_account_id']
            acc_name = row['line_item_usage_account_name']
            cost = row['cost']
            usage = row['usage']
            team_val = row['team_tag']
            owner_val = row['owner_tag']
            is_est = row['is_estimated']

            # Get business context for this account
            ctx = contexts_by_account.get(acc_id, {})
            traffic_vol = float(ctx.get('traffic_volume', 0.0))
            campaign_flag = bool(ctx.get('campaign_flag', False))
            load_test_flag = bool(ctx.get('load_test_flag', False))

            # Retrieve histories for scaling features
            hist_costs, hist_usages, hist_dates = self.feature_store.get_lookback_data(res_id, batch_date, window_days=28)
            
            # Append today's data for temporary rolling metrics
            all_costs = hist_costs + [cost]
            all_usages = hist_usages + [usage]
            
            # Compute MAD and Z-Score
            median_28d = np.median(all_costs)
            mad_28d = np.median(np.abs(all_costs - median_28d))
            cost_z_28d = (cost - median_28d) / (1.4826 * mad_28d + 1e-5)
            
            # Compute 14d change
            cost_change_14d = 0.0
            if len(all_costs) > 14:
                cost_14d_ago = all_costs[-15]
                cost_change_14d = (cost - cost_14d_ago) / (cost_14d_ago + 1e-5)
                
            # Compute age
            age_days = len(hist_dates) + 1
            
            # Compute efficiency
            cost_per_usage = cost / (usage + 1e-5)
            usage_density = usage / 24.0
            
            # Calendar
            day_of_week = pd.to_datetime(batch_date).dayofweek
            is_weekend = 1 if day_of_week >= 5 else 0
            
            # Tags check
            team_missing = 1 if pd.isna(team_val) or team_val == '' else 0
            owner_missing = 1 if pd.isna(owner_val) or owner_val == '' else 0
            
            # Peer ratio
            peer_key = (prod_code, acc_name)
            p_median = peer_medians.get(peer_key, cost)
            peer_ratio = cost / (p_median + 1e-5)

            # Build feature array
            feat_dict = {
                'cost_z_28d': cost_z_28d,
                'cost_change_14d': cost_change_14d,
                'age_days': age_days,
                'cost_per_usage': cost_per_usage,
                'usage_density': usage_density,
                'is_weekend': is_weekend,
                'team_missing': team_missing,
                'owner_missing': owner_missing,
                'peer_ratio': peer_ratio
            }
            
            feat_vector = np.array([feat_dict[f] for f in self.features_list]).reshape(1, -1)
            
            # Scale & Predict
            feat_vector_scaled = self.scaler.transform(feat_vector)
            prob = float(self.model.predict_proba(feat_vector_scaled)[0, 1])

            # --- Apply Business Rules & Confounders Override ---
            anomaly_detected = False
            confidence = prob
            action_strategy = "Auto-Containment"
            immediate_action = "auto-shutdown"
            
            # 1. Base threshold evaluation
            if prob >= self.optimal_threshold:
                anomaly_detected = True

            # 2. Campaign surge / load test bypass (converts to normal/benign)
            if anomaly_detected:
                # If traffic volume is present, calculate cost_per_request normalization
                cost_per_req = cost / max(traffic_vol, 1.0)
                
                # Check history request rates if available (or use simple business flags)
                if campaign_flag or load_test_flag:
                    # Downgrade to normal benign surge
                    anomaly_detected = False
                    action_strategy = "Alert-only (Benign Surge)"
                    immediate_action = "tag-for-review"
                    confidence = 0.10
                
            # 3. Telemetry Delay check
            if anomaly_detected and telemetry_delay_event:
                # If CE-CUR gap is significant, downgrade confidence and force review-only
                confidence = min(confidence, 0.45)
                action_strategy = "Manual Review Required (Telemetry Delay)"
                immediate_action = "tag-for-review"
            
            # 4. Estimated cost check
            if anomaly_detected and is_est:
                # Rule 7.2: Lower confidence to < 0.50, immediate_action = tag-for-review
                confidence = min(confidence, 0.49)
                action_strategy = "Manual Review Required (Estimated Data)"
                immediate_action = "tag-for-review"

            # Save in database
            self.feature_store.update_store(res_id, batch_date, cost, usage)

            if anomaly_detected or prob >= self.optimal_threshold:
                # Create anomaly output record
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
                    'missing_tags': [tag for tag, val in [('resource_tags_user_team', team_missing), ('resource_tags_user_owner', owner_missing)] if val == 1]
                })

        return anomalies_detected
