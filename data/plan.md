# Pipeline Chuan: AWS Cost Anomaly Detection (Leak-Free)

## Muc tieu

Phat hien bat thuong chi phi AWS voi 3 nhan:
- `normal` (0): chi phi binh thuong
- `anomaly` (1): chi phi bat thuong, can dieu tra
- `benign` (2): chi phi cao nhung hop le (peak season, planned scale)

Nguyen tac: **Split truoc - Feature Engineer sau** de tranh data leakage.
Validation thuc hien qua Walk-Forward CV tren tap train. Khong co tap validation co dinh rieng.

---

## Tong quan luong xu ly

```
Raw CSVs (cur_line_items.csv + ec2/rds/ddb/sagemaker/other_metrics.csv)
  |
  v
[Step 1] Load & Merge
         output: df_merged (raw, chua feature engineering)
  |
  v
[Step 2] Temporal Split  80% train | 20% test
         - Sort theo thoi gian, KHONG shuffle
         - KHONG co tap val co dinh
         - Validation = Walk-Forward CV ben trong tap train
         output: df_train_raw, df_test_raw
  |
  v
[Step 3] Leak-Free Feature Engineering
         - fit_stats(df_train_raw) -> train_stats (median, peer stats)
         - transform(df_train_raw, train_stats) -> X_train, y_train
         - transform(df_test_raw, train_stats)  -> X_test, y_test
         Khong dung thong tin tu test de tinh bat ky feature nao
  |
  v
[Step 4] Walk-Forward Cross Validation
         - 5 folds, moi fold fe rieng, model rieng
         - Fold 1: train[0:20%]  -> val[20:40%]
         - Fold 2: train[0:40%]  -> val[40:60%]
         - Fold 3: train[0:60%]  -> val[60:80%]
         - ...
         output: cv_f1_scores, sample_weights, best hyperparams
  |
  v
[Step 5] Train Final XGBoost (Gradient Boosting)
         - fit tren toan bo X_train
         - objective=multi:softprob, num_class=3
         - sample_weight tu class balancing
         - track experiment bang MLflow
         output: model, mlflow_run_id
  |
  v
[Step 6] Threshold Optimization tren CV validation folds
         - Tim best_threshold cho class anomaly
         output: best_threshold
  |
  v
[Step 7] Danh gia model tren X_test
         - F1-macro, ROC-AUC, Confusion Matrix
         - Perturbation Test (Gaussian noise 5%)
         output: test_pred, test_prob, evaluation_df
  |
  v
[Step 8] SHAP Explainability
         - Global: summary_plot, bar_plot cho class anomaly
         - Local: waterfall_plot cho tung mau co prob cao
         output: shap_values, plots
  |
  v
[Step 9] Drift Detection + Retrain Plan
         - KS-test moi feature: X_train vs production window
         - PSI theo thang
         - Kich hoat retrain khi drift > nguong
```

---

## Step 1 - Load & Merge

**Muc tieu:** Tai du lieu thu, tinh CPU features tu hourly columns, join CUR voi Metrics.

**Input:**
- `cur_line_items.csv` - billing data: resource_id, date, cost, usage_amount, tags
- `ec2_metrics.csv`, `rds_metrics.csv`, `ddb_metrics.csv`, `sagemaker_metrics.csv`, `other_services_metrics.csv`

**Xu ly:**
1. Load CUR, parse `line_item_usage_start_date` -> `clean_date` (normalize ve 00:00:00)
2. Voi moi metrics file:
   - Parse `timestamp` -> `clean_date`
   - Tinh `cpu_mean`, `cpu_std`, `cpu_max`, `cpu_min` tu cot `cpu_h0..cpu_h23`
   - Tinh `idle_hours_continuous` = max streak so gio co CPU < 5%
3. Concat tat ca metrics, dedup theo `(resource_id, clean_date)`
4. Inner join CUR + Metrics tren `(resource_id, clean_date)`
5. Sort theo `(line_item_resource_id, clean_date)`
6. Encode label: `{normal:0, anomaly:1, benign:2}`

**Output:** `df_merged` - DataFrame da merge, co cot `label_encoded`, CHUA co rolling features

> **Luu y:** Khong tinh bat ky rolling/lag/cumsum nao o buoc nay.
  Tat ca statistical feature se duoc tinh SAU khi split.

---

## Step 2 - Temporal Split (80/20, khong shuffle)

**Muc tieu:** Chia du lieu theo thoi gian, tranh look-ahead bias.
Khong co tap validation co dinh - validation se thuc hien qua Walk-Forward CV o Step 4.

**Input:** `df_merged`

**Xu ly:**
```python
df_merged = df_merged.sort_values('clean_date').reset_index(drop=True)

n = len(df_merged)
split_idx = int(n * 0.80)

df_train_raw = df_merged.iloc[:split_idx].copy()   # 80% du lieu cu
df_test_raw  = df_merged.iloc[split_idx:].copy()   # 20% du lieu moi nhat
```

**Output:**
- `df_train_raw` - 80% (du lieu cu nhat, dung de train + walk-forward CV)
- `df_test_raw`  - 20% (du lieu moi nhat, chi dung de danh gia cuoi cung)

> **Khong shuffle.** Thu tu thoi gian phai duoc giu nguyen.
  `df_test_raw` khong duoc cham vao cho den Step 7.

---

## Step 3 - Leak-Free Feature Engineering

**Muc tieu:** Tinh cac statistical feature ma KHONG de thong tin tu test ro ri vao train.

**Nguyen tac leak-free:**
- Rolling/lag features: dung `.shift(1)` - chi nhin qua khu, khong nhin hien tai
- Imputation: fit median tren train, ap dung len test
- Peer features: tinh peer_median_cost chi trong pham vi tung split

**Input:** `df_train_raw`, `df_test_raw`

### 3a. Fit stats tren train

```python
def fit_feature_stats(df_train):
    stats = {}
    # Median per resource (dung cho imputation)
    numeric_cols = df_train.select_dtypes(include='number').columns.tolist()
    stats['resource_medians'] = (
        df_train.groupby('line_item_resource_id')[numeric_cols].median()
    )
    stats['global_medians'] = df_train[numeric_cols].median()
    return stats

train_stats = fit_feature_stats(df_train_raw)
```

### 3b. Transform tung split

```python
def transform_features(df_input, stats):
    df = df_input.copy()
    grp = df.groupby('line_item_resource_id')

    # --- Cost temporal (shift(1) tranh leak) ---
    df['rolling_7d_avg']       = grp['line_item_unblended_cost'].transform(
                                     lambda x: x.shift(1).rolling(7, min_periods=1).mean())
    df['rolling_7d_std']       = grp['line_item_unblended_cost'].transform(
                                     lambda x: x.shift(1).rolling(7, min_periods=2).std())
    df['cost_ratio_to_7d_avg'] = df['line_item_unblended_cost'] / (df['rolling_7d_avg'] + 1e-6)
    df['cost_diff']            = df['line_item_unblended_cost'] - df['rolling_7d_avg']
    df['cost_pct_change']      = grp['line_item_unblended_cost'].pct_change()

    rolling_median = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).median())
    rolling_mad = grp['line_item_unblended_cost'].transform(
        lambda x: x.shift(1).rolling(14, min_periods=3).apply(
            lambda y: np.median(np.abs(y - np.median(y))), raw=True))
    df['robust_z'] = 0.6745 * (df['line_item_unblended_cost'] - rolling_median) / (rolling_mad + 1e-6)

    # --- Trend ---
    df['cost_lag_28d']        = grp['line_item_unblended_cost'].shift(28)
    df['cost_pct_change_28d'] = (df['line_item_unblended_cost'] - df['cost_lag_28d']) / (df['cost_lag_28d'] + 1e-6)
    df['slope_14d']           = grp['line_item_unblended_cost'].transform(
                                    lambda x: x.shift(1).rolling(14, min_periods=14).apply(_slope))

    # --- Calendar ---
    df['dayofweek'] = pd.to_datetime(df['clean_date']).dt.dayofweek
    df['month']     = pd.to_datetime(df['clean_date']).dt.month
    df['is_weekend'] = (df['dayofweek'] >= 5).astype(int)

    # --- Usage ---
    df['cost_per_unit_usage'] = df['line_item_unblended_cost'] / (df['line_item_usage_amount'] + 1e-6)
    df['usage_density']       = df['line_item_usage_amount'] / 24
    df['age_days']            = grp.cumcount() + 1

    # --- CPU variance ---
    cpu_cols = [c for c in df.columns if c.startswith('cpu_h')]
    df['cpu_variance_24h'] = df[cpu_cols].var(axis=1) if cpu_cols else 0

    # --- Tag quality ---
    df['team_missing']  = df['resource_tags_user_team'].fillna('MISSING').eq('MISSING (Untagged)').astype(int)
    df['owner_missing'] = df['resource_tags_user_owner'].isna().astype(int)

    # --- Peer ratio (chi trong split nay) ---
    peer_group = ['line_item_usage_account_id', 'line_item_product_code', 'clean_date']
    if all(c in df.columns for c in peer_group):
        df['peer_median_cost'] = df.groupby(peer_group)['line_item_unblended_cost'].transform('median')
        df['peer_ratio']       = df['line_item_unblended_cost'] / (df['peer_median_cost'] + 1e-6)
    else:
        df['peer_ratio'] = 1.0

    # --- Imputation bang stats tu train ---
    numeric_cols = df.select_dtypes(include='number').columns
    df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)
    for col in numeric_cols:
        if df[col].isna().any():
            fallback = stats['global_medians'].get(col, 0)
            per_res  = stats['resource_medians'][col] if col in stats['resource_medians'] else None
            if per_res is not None:
                df[col] = df.apply(
                    lambda r: per_res.get(r['line_item_resource_id'], fallback)
                    if pd.isna(r[col]) else r[col], axis=1)
            else:
                df[col] = df[col].fillna(fallback)
    return df

df_train_fe = transform_features(df_train_raw, train_stats)
df_test_fe  = transform_features(df_test_raw,  train_stats)
```

### 3c. Tach X / y

```python
FEATURE_COLS = [
    'line_item_unblended_cost', 'rolling_7d_avg', 'rolling_7d_std',
    'cost_ratio_to_7d_avg', 'cost_diff', 'cost_pct_change', 'robust_z',
    'slope_14d', 'cost_pct_change_28d', 'age_days',
    'dayofweek', 'month', 'is_weekend',
    'line_item_usage_amount', 'usage_density', 'cost_per_unit_usage',
    'cpu_mean', 'cpu_std', 'cpu_max', 'cpu_min', 'cpu_variance_24h',
    'idle_hours_continuous', 'memory_mib', 'network_in_bytes',
    'network_out_bytes', 'disk_io_ops',
    'team_missing', 'owner_missing', 'peer_ratio'
]
features = [f for f in FEATURE_COLS if f in df_train_fe.columns]

X_train, y_train = df_train_fe[features], df_train_fe['label_encoded']
X_test,  y_test  = df_test_fe[features],  df_test_fe['label_encoded']
```

**Output:** `X_train`, `y_train`, `X_test`, `y_test`

---

## Step 4 - Walk-Forward Cross Validation

**Muc tieu:** Validate pipeline leak-free, tim hyperparams tot, tinh sample_weights.
Walk-forward la kieu CV phu hop nhat cho time-series: fold sau luon dung du lieu moi hon fold truoc.

**Nguyen tac Walk-Forward:**
```
Fold 1:  [===Train===]  [Val]  ....  ....  ....
Fold 2:  [=====Train=====]  [Val]  ....  ....
Fold 3:  [=======Train=======]  [Val]  ....
Fold 4:  [=========Train=========]  [Val]  .
Fold 5:  [===========Train===========]  [Val]
```
- Tap train luon o phia truoc (qua khu), val o phia sau (tuong lai)
- Moi fold: fit_stats() tren fold_train, transform() rieng biet -> tranh leak giua cac fold
- KHONG dung `TimeSeriesSplit` tren du lieu da feature-engineered
  phai apply tren raw data roi moi engineer

**Input:** `df_train_raw`

**Xu ly:**
```python
from sklearn.model_selection import TimeSeriesSplit
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier
from sklearn.metrics import f1_score

tscv = TimeSeriesSplit(n_splits=5)
cv_scores = []

for fold, (train_idx, val_idx) in enumerate(tscv.split(df_train_raw)):
    fold_train = df_train_raw.iloc[train_idx].copy()
    fold_val   = df_train_raw.iloc[val_idx].copy()

    # Feature engineer RIENG tung fold
    fold_stats   = fit_feature_stats(fold_train)
    X_tr = transform_features(fold_train, fold_stats)
    X_vl = transform_features(fold_val,   fold_stats)

    features_fold = [f for f in FEATURE_COLS if f in X_tr.columns]
    y_tr = X_tr['label_encoded'];  X_tr = X_tr[features_fold]
    y_vl = X_vl['label_encoded'];  X_vl = X_vl[features_fold]

    sw = compute_sample_weight('balanced', y=y_tr)

    clf = XGBClassifier(
        objective='multi:softprob', num_class=3,
        eval_metric='mlogloss',
        learning_rate=0.05, n_estimators=400,
        max_depth=5, subsample=0.8, colsample_bytree=0.8,
        random_state=42, tree_method='hist', n_jobs=-1
    )
    clf.fit(X_tr, y_tr, sample_weight=sw)
    pred = clf.predict(X_vl)

    score = f1_score(y_vl, pred, average='macro')
    cv_scores.append(score)
    print(f'Fold {fold+1} F1-macro: {score:.4f}')

print(f'CV F1-macro: {np.mean(cv_scores):.4f} +/- {np.std(cv_scores):.4f}')

# Sample weights cho final model
sample_weights = compute_sample_weight('balanced', y=y_train)
```

**Output:** `cv_scores`, `sample_weights`

---

## Step 5 - Train Final XGBoost (Gradient Boosting)

**Muc tieu:** Train model phan loai 3 nhan tren toan bo X_train bang Gradient Boosting.

**Khai niem Gradient Boosting trong XGBoost:**
- XGBoost xay dung cac cay quyet dinh **tuan tu** (sequential trees)
- Moi cay moi hoc cach sua lai loi (residual/gradient) cua tap cac cay truoc do
- Cong thuc: `F_m(x) = F_{m-1}(x) + learning_rate * h_m(x)`
  trong do `h_m` la cay thu m fit tren pseudo-residuals
- `objective=multi:softprob`: tinh xac suat cho ca 3 nhan, dung cross-entropy loss
- `n_estimators=600`: so cay (so buoc boosting)
- `learning_rate=0.05`: shrinkage - moi cay chi dong gop mot phan nho
- `max_depth=5`: gioi han do phuc tap tung cay
- `subsample`, `colsample_bytree`: stochastic boosting - lay ngau nhien subset data/feature moi cay
- `reg_alpha`, `reg_lambda`: L1/L2 regularization tranh overfit
- `scale_pos_weight`: xu ly class imbalance cho class anomaly
- `tree_method=hist`: xap xi histogram, huan luyen nhanh hon voi du lieu lon

**Input:** `X_train`, `y_train`, `sample_weights`

**Xu ly:**
```python
import mlflow
import mlflow.xgboost
from xgboost import XGBClassifier

mlflow.set_experiment('AWS_Cost_Anomaly_Detection_LeakFree')

with mlflow.start_run(run_name='XGBoost_Boosting_Final'):

    scale_pos_weight = (len(y_train) - (y_train==1).sum()) / (y_train==1).sum()

    model = XGBClassifier(
        objective='multi:softprob',
        num_class=3,
        eval_metric='mlogloss',
        learning_rate=0.05,
        n_estimators=600,
        max_depth=5,
        min_child_weight=5,
        subsample=0.75,
        colsample_bytree=0.75,
        reg_alpha=0.5,
        reg_lambda=1.5,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
        tree_method='hist',
        n_jobs=-1
    )

    model.fit(
        X_train, y_train,
        sample_weight=sample_weights,
        verbose=50
    )

    mlflow.log_params(model.get_params())
    mlflow.log_metric('cv_f1_mean', np.mean(cv_scores))
    mlflow.log_metric('cv_f1_std',  np.std(cv_scores))
    mlflow.xgboost.log_model(model, 'model')
```

**Output:** `model`, MLflow run logged

---

## Step 6 - Threshold Optimization

**Muc tieu:** Tim nguong phan loai toi uu cho class `anomaly` tren tap CV validation.
Model tra ve xac suat 3 class; can chon nguong de quyet dinh nhan cuoi cung.

**Input:** `model`, walk-forward CV val folds

**Xu ly:**
```python
from sklearn.metrics import f1_score, precision_score, recall_score

# Gom ket qua tu tat ca val folds
all_val_prob = []
all_val_true = []
for fold_val_prob, fold_val_true in cv_val_results:
    all_val_prob.extend(fold_val_prob[:, 1])  # prob class anomaly
    all_val_true.extend(fold_val_true)

thresholds = np.arange(0.05, 0.95, 0.01)
results = []
for th in thresholds:
    pred = (np.array(all_val_prob) >= th).astype(int)
    results.append({
        'Threshold': th,
        'F1':        f1_score(np.array(all_val_true)==1, pred, zero_division=0),
        'Precision': precision_score(np.array(all_val_true)==1, pred, zero_division=0),
        'Recall':    recall_score(np.array(all_val_true)==1, pred, zero_division=0),
    })

threshold_df   = pd.DataFrame(results)
best_threshold = threshold_df.loc[threshold_df['F1'].idxmax(), 'Threshold']
print(f'Best Threshold: {best_threshold:.3f}')
```

**Output:** `best_threshold`, `threshold_df`

---

## Step 7 - Danh gia model tren X_test

**Muc tieu:** Danh gia hieu suat thuc te tren 20% du lieu moi nhat chua tung duoc dung.

**Input:** `model`, `X_test`, `y_test`, `best_threshold`

**Xu ly:**
```python
from sklearn.metrics import (
    classification_report, roc_auc_score,
    ConfusionMatrixDisplay, RocCurveDisplay, PrecisionRecallDisplay
)

# Predict tren test set
test_prob_all = model.predict_proba(X_test)          # shape (n, 3)
test_prob     = test_prob_all[:, 1]                  # prob class anomaly
test_pred     = (test_prob >= best_threshold).astype(int)

# 1. Classification report 3 class
print(classification_report(y_test, model.predict(X_test), digits=4))

# 2. ROC-AUC (binary: anomaly vs rest)
auc = roc_auc_score(y_test == 1, test_prob)
print(f'ROC AUC: {auc:.4f}')

# 3. Confusion Matrix
ConfusionMatrixDisplay.from_predictions(y_test == 1, test_pred)

# 4. Perturbation Test - kiem tra model co bi anh huong nhieu khong
noise_level = 0.05
X_test_noisy = X_test.copy()
for col in ['cpu_mean', 'cpu_std', 'memory_mib', 'network_in_bytes', 'network_out_bytes']:
    if col in X_test_noisy.columns:
        noise = np.random.normal(0, noise_level * X_test_noisy[col].std(), len(X_test_noisy))
        X_test_noisy[col] += noise
noisy_pred = (model.predict_proba(X_test_noisy)[:, 1] >= best_threshold).astype(int)
print('F1 drop from noise:', f1_score(y_test==1, test_pred) - f1_score(y_test==1, noisy_pred))
```

**Output:** `test_pred`, `test_prob`, `auc`, `evaluation_df`

---

## Step 8 - SHAP Explainability

**Muc tieu:** Giai thich model - feature nao anh huong nhieu nhat vao quyet dinh anomaly.
Dung cho debug, audit, va trao doi voi team FinOps.

**Input:** `model`, `X_test`, `test_prob`

### 8a. Global SHAP - Danh gia tong the

```python
import shap

explainer = shap.TreeExplainer(model)
X_sample  = X_test.sample(min(1000, len(X_test)), random_state=42)
shap_values = explainer.shap_values(X_sample)
# shap_values la list 3 phan tu [class0, class1, class2]
shap_anomaly = shap_values[1]  # lay class anomaly

# Summary plot: beeswarm - phan phoi muc do anh huong tung feature
shap.summary_plot(shap_anomaly, X_sample)

# Bar plot: feature importance trung binh
shap.summary_plot(shap_anomaly, X_sample, plot_type='bar')
```

### 8b. Local SHAP - Giai thich tung mau anomaly cu the

```python
# Lay mau co xac suat anomaly cao nhat
idx    = np.argmax(test_prob)
sample = X_test.iloc[[idx]]
sample_shap = explainer.shap_values(sample)

# Waterfall plot: feature nao day len / keo xuong quyet dinh anomaly
shap.waterfall_plot(
    shap.Explanation(
        values      = sample_shap[1][0],
        base_values = explainer.expected_value[1],
        data        = sample.iloc[0],
        feature_names = X_test.columns.tolist()
    )
)
```

**Doc ket qua SHAP:**
- Feature co SHAP > 0: day xac suat ve phia anomaly
- Feature co SHAP < 0: keo xac suat ve phia normal/benign
- Mau do = gia tri feature cao, mau xanh = gia tri thap
- Dung de hieu tai sao mot resource bi flag la anomaly

**Output:** `shap_values`, global plot, local waterfall plot

---

## Step 9 - Drift Detection + Retrain Plan

**Muc tieu:** Phat hien khi phan phoi du lieu thay doi de kich hoat retrain ung thi.

### 9a. Drift Detection bang KS-Test

**Input:** `X_train` (reference), production data (sliding window 30 ngay)

```python
from scipy.stats import ks_2samp

def detect_drift(X_reference, X_production, threshold=0.05):
    drift_results = []
    for col in X_reference.select_dtypes(include=np.number).columns:
        stat, p_value = ks_2samp(X_reference[col].dropna(), X_production[col].dropna())
        drift_results.append({
            'Feature':      col,
            'KS_Statistic': stat,
            'P_Value':      p_value,
            'Drift':        'Yes' if p_value < threshold else 'No'
        })
    drift_df = pd.DataFrame(drift_results).sort_values('P_Value')
    drift_pct = (drift_df['Drift'] == 'Yes').mean()
    return drift_df, drift_pct

drift_df, drift_pct = detect_drift(X_train, X_production_window)
print(f'Features bi drift: {drift_pct:.1%}')
```

### 9b. Drift Detection bang PSI (Population Stability Index)

PSI do su thay doi phan phoi theo thang, phu hop hon KS cho theo doi lien tuc.

```python
def calculate_psi(expected, actual, buckets=10):
    breakpoints = np.percentile(expected, np.linspace(0, 100, buckets + 1))
    breakpoints[0]  = -np.inf
    breakpoints[-1] =  np.inf

    expected_counts = np.histogram(expected, breakpoints)[0] / len(expected)
    actual_counts   = np.histogram(actual,   breakpoints)[0] / len(actual)

    # Tranh log(0)
    expected_counts = np.where(expected_counts == 0, 1e-4, expected_counts)
    actual_counts   = np.where(actual_counts   == 0, 1e-4, actual_counts)

    psi = np.sum((actual_counts - expected_counts) * np.log(actual_counts / expected_counts))
    return psi

# Danh gia PSI hang thang cho top features
for col in top_features:
    psi = calculate_psi(X_train[col].dropna(), X_production_month[col].dropna())
    print(f'{col}: PSI = {psi:.4f}', '-> DRIFT' if psi > 0.2 else '')
```

**Nguong PSI:**
- PSI < 0.1: Phan phoi on dinh
- 0.1 <= PSI < 0.2: Can theo doi
- PSI >= 0.2: Drift nghiem trong, can retrain

### 9c. Dieu kien kich hoat Retrain

Retrain duoc kich hoat khi thoa MAN mot trong cac dieu kien sau:

| Dieu kien | Nguong | Hanh dong |
|-----------|--------|-----------|
| KS-test drift | > 30% features co p < 0.05 | Alert + schedule retrain |
| PSI trung binh top-10 features | > 0.2 | Retrain ngay |
| F1-macro tren production labels | Giam > 5% so voi baseline | Retrain ngay |
| Thoi gian | Moi 30 ngay | Retrain dinh ky |

### 9d. Ke hoach Retrain

```
Trigger (bat ky dieu kien tren)
  |
  v
[R1] Thu thap du lieu moi
     - Lay production data tu thoi diem retrain truoc den hien tai
     - Verify labels (manual review hoac weak supervision)
  |
  v
[R2] Tao dataset moi
     - Append du lieu moi vao df_train_raw cu
     - Re-split: 80% train / 20% test theo ngay
     - Dam bao class distribution hop ly
  |
  v
[R3] Chay lai tu Step 3 den Step 8
     - fit_feature_stats() tren train moi
     - Walk-forward CV 5 folds
     - Train XGBoost moi voi MLflow experiment moi
     - So sanh cv_f1 voi model cu: chi deploy neu tot hon
  |
  v
[R4] Deploy model moi
     - Luu model vao MLflow Model Registry
     - Cap nhat train_stats moi (dung cho transform production)
     - Cap nhat best_threshold
     - Log retrain event vao retrain_log.csv
  |
  v
[R5] Reset baseline
     - X_reference = X_train moi
     - Reset drift counters
     - Tiep tuc monitoring
```

**Output:** `retrain_log.csv`, model moi tren MLflow Registry

---

## Tóm tắt thứ tự thực thi

| Buoc | Mo ta | Input | Output |
|------|-------|-------|--------|
| 1 | Load & Merge | raw CSVs | `df_merged` |
| 2 | Temporal Split 80/20 | `df_merged` | `df_train_raw`, `df_test_raw` |
| 3 | Feature Engineering | raw splits + `train_stats` | `X_train`, `X_test`, `y_*` |
| 4 | Walk-Forward CV (5 folds) | `df_train_raw` | `cv_scores`, `sample_weights` |
| 5 | Train XGBoost Boosting | `X_train`, `y_train` | `model` (MLflow) |
| 6 | Threshold Optimization | `model`, CV val probs | `best_threshold` |
| 7 | Evaluate tren X_test | `model`, `X_test` | metrics, plots |
| 8 | SHAP Explainability | `model`, `X_test` | global + local plots |
| 9 | Drift Detection + Retrain | `X_train`, production data | drift report, retrain trigger |

---

## So sanh voi DETECT_3 (da sua)

| Van de | DETECT_3 (leaky) | Pipeline nay (chuan) |
|--------|-----------------|---------------------|
| Feature engineering | Tren toan bo dataset truoc split | Sau split, fit stats chi tren train |
| Rolling/lag | Tinh xong roi moi chia | .shift(1) + tinh trong tung split |
| Imputation | groupby.transform(median) toan dataset | Dung median tu train fill val/test |
| Peer ratio | groupby toan dataset | Chi trong pham vi tung split |
| Walk-forward CV | Goi FE(val, is_train=False) khong co train_stats | Fit fold_stats tren fold_train truoc |
| Val co dinh | Co tap val rieng (15%) | Khong, validation = walk-forward CV |
| 3 nhan | Co | Co, ro hon: normal/anomaly/benign |
| Drift + Retrain | Khong co | Co day du KS-test, PSI, ke hoach retrain |

---

*Pipeline tham khao: data/plan.md - AWS Cost Anomaly Detection v3 Leak-Free*
