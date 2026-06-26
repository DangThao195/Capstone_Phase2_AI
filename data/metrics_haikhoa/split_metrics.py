import json
import pandas as pd
import os

# Determine execution directory paths
data_dir = os.path.dirname(os.path.abspath(__file__))
metrics_dir = os.path.join(data_dir, "metrics")
metrics_path = os.path.join(metrics_dir, "resource_metrics.json")
cur_path = os.path.join(data_dir, "cur_line_items.csv")

# Ensure metrics directory exists
os.makedirs(metrics_dir, exist_ok=True)

# Load metrics JSON
with open(metrics_path, 'r', encoding='utf-8') as f:
    data = json.load(f)

# Load cur to mapping product codes
cur = pd.read_csv(cur_path)
res_to_product = cur.groupby('line_item_resource_id')['line_item_product_code'].first().to_dict()

# Flatten hourly CPU and construct rows
flattened_rows = []
for r in data:
    row = {
        "resource_id": r["resource_id"],
        "timestamp": r["timestamp"],
        "cpu_percent": r["cpu_percent"],
        "memory_mib": r["memory_mib"],
        "network_in_bytes": r["network_in_bytes"],
        "network_out_bytes": r["network_out_bytes"],
        "disk_io_ops": r["disk_io_ops"],
        "database_connections": r["database_connections"],
        "gpu_utilization": r["gpu_utilization"],
        "label": r["label"]
    }
    # Add hourly CPU columns
    for h, val in enumerate(r["cpu_utilization_hourly"]):
        row[f"cpu_h{h}"] = val
    flattened_rows.append(row)

df = pd.DataFrame(flattened_rows)

# Map service from CUR
df['product_code'] = df['resource_id'].map(res_to_product)
# Fallback for log groups
df.loc[df['product_code'].isna() & df['resource_id'].str.contains('log-group', case=False), 'product_code'] = 'AmazonCloudWatch'

# Define groups
groups = {
    "ec2_metrics.csv": df[df['product_code'] == 'AmazonEC2'],
    "rds_metrics.csv": df[df['product_code'] == 'AmazonRDS'],
    "sagemaker_metrics.csv": df[df['product_code'] == 'AmazonSageMaker'],
    "ddb_metrics.csv": df[df['product_code'] == 'AmazonDynamoDB'],
    "other_services_metrics.csv": df[~df['product_code'].isin(['AmazonEC2', 'AmazonRDS', 'AmazonSageMaker', 'AmazonDynamoDB'])]
}

print("Splitting statistics:")
for filename, subset in groups.items():
    if len(subset) == 0:
        continue
    # Drop product_code column from the final CSV
    subset_csv = subset.drop(columns=['product_code'])
    
    # Drop columns that are completely null in this subset
    null_cols = subset_csv.columns[subset_csv.isnull().all()].tolist()
    subset_csv = subset_csv.drop(columns=null_cols)
    
    output_csv_path = os.path.join(metrics_dir, filename)
    subset_csv.to_csv(output_csv_path, index=False, encoding='utf-8')
    print(f"  Saved {filename} | Rows: {len(subset_csv)} | Columns: {len(subset_csv.columns)}")

print("\nDone splitting JSON metrics into CSV files!")
