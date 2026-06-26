import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import classification_report, confusion_matrix, precision_recall_curve
import os

# --- 1. Load Data ---
data_dir = r"d:\Xbrain\Capstone-AIOps-02\data"
cur_path = os.path.join(data_dir, "cur_line_items.csv")

print("Loading cur_line_items.csv...")
df_raw = pd.read_csv(cur_path)
df_raw['date'] = pd.to_datetime(df_raw['line_item_usage_start_date']).dt.date

# Sort chronologically
df_raw = df_raw.sort_values(by=['line_item_resource_id', 'date']).reset_index(drop=True)

# --- 2. Labeling Ground-Truth Anomalies & Confounders (At resource-day level) ---
# Map resource_id -> description/type for ground truth annotations
anomalies_map = {
    "i-0untaggedfleet01": ("anomaly", "untagged_spend", pd.to_datetime("2026-03-01").date(), pd.to_datetime("2026-05-31").date()),
    "ddb-table-events-prod": ("anomaly", "gradual_drift", pd.to_datetime("2026-04-01").date(), pd.to_datetime("2026-05-31").date()),
    "db-staging-orphan-01": ("anomaly", "idle_resource", pd.to_datetime("2026-03-20").date(), pd.to_datetime("2026-05-31").date()),
    "vol-0orphans-aggregate": ("anomaly", "idle_resource", pd.to_datetime("2026-03-01").date(), pd.to_datetime("2026-05-31").date()),
    "natgw-misconfig-spike": ("anomaly", "sudden_spike", pd.to_datetime("2026-05-12").date(), pd.to_datetime("2026-05-16").date()),
    "log-group-debug-runaway": ("anomaly", "sudden_spike", pd.to_datetime("2026-04-28").date(), pd.to_datetime("2026-05-04").date())
}

# 3 Confounder / Benign events (labeled as 0/Normal to avoid FP alerts)
benign_map = {
    "i-0flashsale-autoscale": ("benign", "flash_sale", pd.to_datetime("2026-05-23").date(), pd.to_datetime("2026-05-26").date()),
    "migration-egress-onetime": ("benign", "migration_egress", pd.to_datetime("2026-03-28").date(), pd.to_datetime("2026-03-30").date()),
    "i-0loadtest-fleet": ("benign", "load_test", pd.to_datetime("2026-05-06").date(), pd.to_datetime("2026-05-07").date())
}

def get_row_label(row):
    res_id = row['line_item_resource_id']
    row_date = row['date']
    
    # GPU cluster resources start with i-0fbgpu
    if isinstance(res_id, str) and res_id.startswith("i-0fbgpu"):
        if pd.to_datetime("2026-04-08").date() <= row_date <= pd.to_datetime("2026-04-25").date():
            return 1, "runaway_usage"
            
    if isinstance(res_id, str) and "db-staging-orphan-01" in res_id:
        if pd.to_datetime("2026-03-20").date() <= row_date <= pd.to_datetime("2026-05-31").date():
            return 1, "idle_resource"
            
    if res_id in anomalies_map:
        lbl_type, anomaly_type, start, end = anomalies_map[res_id]
        if start <= row_date <= end:
            return 1, anomaly_type
            
    if res_id in benign_map:
        lbl_type, benign_type, start, end = benign_map[res_id]
        if start <= row_date <= end:
            return 0, benign_type
            
    return 0, "normal"

# Group raw CUR to resource-day aggregation
print("Aggregating CUR log entries to resource-day granularity...")
df_res_day = df_raw.groupby(['line_item_resource_id', 'date', 'line_item_product_code', 'line_item_usage_account_name']).agg(
    cost=('line_item_unblended_cost', 'sum'),
    usage=('line_item_usage_amount', 'sum'),
    team_tag=('resource_tags_user_team', 'first'),
    owner_tag=('resource_tags_user_owner', 'first')
).reset_index()
df_res_day['is_estimated'] = False

# Apply labels
labels_res = df_res_day.apply(get_row_label, axis=1)
df_res_day['target'] = [x[0] for x in labels_res]
df_res_day['event_type'] = [x[1] for x in labels_res]

print(f"Total resource-day data points: {len(df_res_day)}")

# --- 3. Feature Engineering (BEHAVIORAL ONLY) ---
print("Computing behavioral features...")
df_res_day = df_res_day.sort_values(by=['line_item_resource_id', 'date']).reset_index(drop=True)

MAD_CONSTANT = 1.4826

def compute_mad_features(group):
    median_28d = group['cost'].rolling(window=28, min_periods=1).median()
    mad_28d = group['cost'].rolling(window=28, min_periods=1).apply(
        lambda x: np.median(np.abs(x - np.median(x))), raw=True
    )
    cost_z = (group['cost'] - median_28d) / (MAD_CONSTANT * mad_28d + 1e-5)
    cost_14d_ago = group['cost'].shift(14)
    cost_change_14d = (group['cost'] - cost_14d_ago) / (cost_14d_ago + 1e-5)
    age_days = np.arange(len(group)) + 1
    
    group['rolling_median_28d'] = median_28d
    group['rolling_mad_28d'] = mad_28d
    group['cost_z_28d'] = cost_z
    group['cost_change_14d'] = cost_change_14d
    group['age_days'] = age_days
    return group

df_res_day = df_res_day.groupby('line_item_resource_id', group_keys=False).apply(compute_mad_features)

df_res_day['cost_per_usage'] = df_res_day['cost'] / (df_res_day['usage'] + 1e-5)
df_res_day['usage_density'] = df_res_day['usage'] / 24.0

df_res_day['date_obj'] = pd.to_datetime(df_res_day['date'])
df_res_day['day_of_week'] = df_res_day['date_obj'].dt.dayofweek
df_res_day['is_weekend'] = (df_res_day['day_of_week'] >= 5).astype(int)

df_res_day['team_missing'] = (df_res_day['team_tag'].isna() | (df_res_day['team_tag'] == '')).astype(int)
df_res_day['owner_missing'] = (df_res_day['owner_tag'].isna() | (df_res_day['owner_tag'] == '')).astype(int)

def compute_peer_median(group):
    day_median = group.groupby('date')['cost'].transform('median')
    group['peer_ratio'] = group['cost'] / (day_median + 1e-5)
    return group

df_res_day = df_res_day.groupby(['line_item_product_code', 'line_item_usage_account_name'], group_keys=False).apply(compute_peer_median)

features_list = [
    'cost_z_28d', 'cost_change_14d', 'age_days', 
    'cost_per_usage', 'usage_density', 'is_weekend', 
    'team_missing', 'owner_missing', 'peer_ratio'
]
df_res_day[features_list] = df_res_day[features_list].fillna(0)

# --- 4. Chronological / Temporal Split ---
print("Splitting dataset chronologically...")
train_mask = df_res_day['date_obj'] <= pd.to_datetime("2026-04-30")
test_mask = df_res_day['date_obj'] > pd.to_datetime("2026-04-30")

df_train = df_res_day[train_mask].copy()
df_test = df_res_day[test_mask].copy()

X_train = df_train[features_list].values
y_train = df_train['target'].values

X_test = df_test[features_list].values
y_test = df_test['target'].values

# --- 5. Model Scaling & Training ---
scaler = RobustScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

neg_count = (y_train == 0).sum()
pos_count = (y_train == 1).sum()
scale_pos_weight = neg_count / max(pos_count, 1)

model = xgb.XGBClassifier(
    n_estimators=400,
    max_depth=4,
    learning_rate=0.03,
    subsample=0.8,
    colsample_bytree=0.7,
    reg_alpha=1.0,
    reg_lambda=2.0,
    min_child_weight=3,
    scale_pos_weight=scale_pos_weight,
    eval_metric='aucpr',
    random_state=42
)
model.fit(X_train_scaled, y_train)

# --- 6. Dynamic Threshold Selection on Validation Split ---
y_train_prob = model.predict_proba(X_train_scaled)[:, 1]
precisions, recalls, thresholds = precision_recall_curve(y_train, y_train_prob)

valid_threshold_indices = np.where(precisions[:-1] >= 0.80)[0]
if len(valid_threshold_indices) > 0:
    chosen_idx = valid_threshold_indices[np.argmax(recalls[valid_threshold_indices])]
    optimal_threshold = thresholds[chosen_idx]
else:
    optimal_threshold = 0.50

print(f"Optimal decision threshold selected: {optimal_threshold:.4f}")

# --- 7. Evaluation on Test Set ---
y_test_prob = model.predict_proba(X_test_scaled)[:, 1]
y_test_pred = (y_test_prob >= optimal_threshold).astype(int)

print("\n==================== TEST SET PERFORMANCE REPORT ====================")
print(classification_report(y_test, y_test_pred, target_names=['Normal', 'Anomaly'], zero_division=0))

cm = confusion_matrix(y_test, y_test_pred)
print("Confusion Matrix:")
print(cm)

tn, fp, fn, tp = cm.ravel()
precision = tp / (tp + fp) if (tp + fp) > 0 else 0
fpr = fp / (fp + tn) if (fp + tn) > 0 else 0
recall = tp / (tp + fn) if (tp + fn) > 0 else 0
f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

print(f"\nPrecision (TP / (TP + FP)): {precision:.4f} (Target: >= 80%)")
print(f"False Positive Rate (FP / (FP + TN)): {fpr:.4f} (Target: <= 10%)")

# --- 8. Breakdown by Anomaly Type & Confounders Check ---
df_test['pred_label'] = y_test_pred
df_test['pred_prob'] = y_test_prob

print("\n--- Detection Coverage per Anomaly Event (Test Set - May) ---")
test_events = df_test.groupby('event_type')
for name, group in test_events:
    tps = group[(group['target'] == 1) & (group['pred_label'] == 1)]
    fps = group[(group['target'] == 0) & (group['pred_label'] == 1)]
    
    if name != 'normal' and not name.startswith('benign'):
        event_cnt = len(group)
        caught_cnt = len(tps)
        pct = (caught_cnt / event_cnt) * 100 if event_cnt > 0 else 0
        print(f"Anomaly [{name:15s}]: Caught {caught_cnt:3d} / {event_cnt:3d} resource-days ({pct:6.2f}%)")
    elif name.startswith('benign'):
        benign_cnt = len(group)
        triggered_cnt = len(fps)
        pct = (triggered_cnt / benign_cnt) * 100 if benign_cnt > 0 else 0
        print(f"Benign  [{name:15s}]: Alerted {triggered_cnt:3d} / {benign_cnt:3d} resource-days ({pct:6.2f}%) - TARGET IS 0%")

# --- 9. Feature Importance via XGBoost ---
importance = model.feature_importances_
df_importance = pd.DataFrame({
    'Feature': features_list,
    'Importance': importance
}).sort_values(by='Importance', ascending=False).reset_index(drop=True)

print("\n[XGBOOST FEATURE IMPORTANCE RANKING]:")
print(df_importance.to_string())

# Save refined scaler & model parameters
import pickle
serving_dir = os.path.join(data_dir, "serving_models")
os.makedirs(serving_dir, exist_ok=True)

with open(os.path.join(serving_dir, "robust_scaler.pkl"), "wb") as f:
    pickle.dump(scaler, f)

model.save_model(os.path.join(serving_dir, "xgboost_anomaly_detector.json"))
with open(os.path.join(serving_dir, "optimal_threshold.txt"), "w") as f:
    f.write(str(optimal_threshold))
with open(os.path.join(serving_dir, "features_list.txt"), "w") as f:
    f.write(",".join(features_list))

print(f"\nRefined model and scaler saved to {serving_dir}")
