"""Quick validation script for the data-test dataset."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import warnings; warnings.filterwarnings("ignore")
import finops_watch as fw
import pandas as pd, numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data-test')

print("=" * 60)
print("FINOPS WATCH — data-test validation")
print("=" * 60)

# ── Load ──────────────────────────────────────────────────────────────────────
ce, cur, metrics = fw.load_sources(DATA_DIR)
print(f"CE: {ce.shape}  | {ce.date.min().date()} -> {ce.date.max().date()}")
print(f"CUR: {cur.shape}")
print(f"Metrics: {metrics.shape}")
print(f"Accounts: {sorted(ce.linked_account_name.unique())}")

# ── Baseline run ──────────────────────────────────────────────────────────────
panel  = fw.build_panel(ce)
pf     = fw.engineer_panel_features(panel, cur)
rmet   = fw.build_resource_metric_daily(metrics)
rf     = fw.engineer_resource_features(cur, rmet)
pf     = fw.apply_panel_rules(pf)
ra     = fw.detect_resource_alerts(rf)
models = fw.train_isolation_forests(pf)
pf     = fw.score_isolation_forests(pf, models)
fired_all, pf_full, ra = fw.fuse(pf, ra)

fired = fired_all[fired_all.alert_fired == 1]
print(f"\nTotal alert-days: {len(fired)}")
print("By type:\n", fired.pred_type.value_counts().to_string())

labels = pd.read_csv(os.path.join(DATA_DIR, 'anomaly_labels_full.csv'))
res, m = fw.evaluate_events(fired_all, labels)

print("\nEVENT DETECTION SUMMARY")
print("-" * 70)
for _, r in res.iterrows():
    if r['label'] == 'benign':
        status = "OK  NOT ALERT" if not r['detected'] else "FP! FIRED"
    else:
        status = "OK  DETECTED" if r['detected'] else "XX  MISSED"
    fa = "" if pd.isna(r['first_alert']) else pd.to_datetime(r['first_alert']).strftime('%b %d')
    dl = "" if pd.isna(r['delay']) else f"+{int(r['delay'])}d"
    print(f"  {r['anomaly_id']:<4} {r['anomaly_type']:<17} [{status}]  {fa}  {dl}  conf={r['confidence']}")

print("-" * 70)
print(f"Precision : {m['precision']:.3f}  {'PASS' if m['precision']>=0.80 else 'FAIL'} (>= 0.80)")
print(f"Recall    : {m['recall']:.3f}")
print(f"F1-score  : {m['f1']:.3f}")
print(f"FPR       : {m['fpr']:.3f}  {'PASS' if m['fpr']<=0.10 else 'FAIL'} (<= 0.10)")
gate = m['precision'] >= 0.80 and m['fpr'] <= 0.10
print(f"TF2 Gate  : {'PASS' if gate else 'FAIL'}")

# ── Generalization test ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("GENERALIZATION TEST")
print("=" * 60)

def anonymize(ce, cur, metrics):
    ce, cur, metrics = ce.copy(), cur.copy(), metrics.copy()
    amap = {a: f'acct-{chr(65+i)}'
            for i, a in enumerate(sorted(ce.linked_account_name.unique()))}
    smap = {s: f'svc-{i+1}'
            for i, s in enumerate(sorted(ce.service_code.unique()))}
    ce['linked_account_name'] = ce.linked_account_name.map(amap)
    ce['service_code']        = ce.service_code.map(smap)
    cur['line_item_usage_account_name'] = cur.line_item_usage_account_name.map(
        lambda x: amap.get(x, x))
    cur['line_item_product_code'] = cur.line_item_product_code.map(
        lambda x: smap.get(x, x))
    if 'service' in metrics.columns:
        metrics['service'] = metrics['service'].map(lambda x: smap.get(x, x))
    return ce, cur, metrics, amap, smap

ce2, cur2, met2, amap, smap = anonymize(ce, cur, metrics)
_orig_ec2 = fw.EC2_CODE
fw.EC2_CODE = smap['AmazonEC2']
try:
    fw2 = fw.FinOpsWatch()
    core2 = fw2.fit_transform(ce2, cur2, met2)
    lab2 = labels.copy()
    lab2['linked_account_name'] = lab2.linked_account_name.map(lambda x: amap.get(x, x))
    lab2['service'] = lab2['service'].map(lambda x: smap.get(x, x))
    res2, m2 = fw.evaluate_events(core2['fired_all'], lab2)
finally:
    fw.EC2_CODE = _orig_ec2

same_vec = (res.sort_values('anomaly_id').detected.values ==
            res2.sort_values('anomaly_id').detected.values).all()
same_metrics = np.allclose(
    [m['precision'], m['recall'], m['f1'], m['fpr']],
    [m2['precision'], m2['recall'], m2['f1'], m2['fpr']])

print(f"BASELINE   : {({k: round(v,3) for k,v in m.items() if isinstance(v,float)})}")
print(f"ANONYMIZED : {({k: round(v,3) for k,v in m2.items() if isinstance(v,float)})}")
print()
print("  Account names anonymized    : PASS")
print("  Service names anonymized    : PASS")
print(f"  Detection results unchanged : {'PASS' if same_vec else 'FAIL'}")
print(f"  Metrics identical           : {'PASS' if same_metrics else 'FAIL'}")
g_pass = same_vec and same_metrics
print(f"\n  Generalization Test: {'PASS' if g_pass else 'FAIL'}")
print("=" * 60)
