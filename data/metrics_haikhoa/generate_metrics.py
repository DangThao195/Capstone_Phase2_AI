import pandas as pd
import numpy as np
import json
import os
import hashlib

# Determine execution directory paths
data_dir = os.path.dirname(os.path.abspath(__file__))
cur_path = os.path.join(data_dir, "cur_line_items.csv")
cur = pd.read_csv(cur_path, parse_dates=["line_item_usage_start_date"])
cur['date_str'] = cur['line_item_usage_start_date'].dt.strftime('%Y-%m-%d')

# Ground truth anomalies and benign
gt_anomalies = {
    "arn:aws:rds:us-east-1:acct:db:db-staging-orphan-01": ("2026-03-20", "2026-05-31"),
    "log-group-debug-runaway": ("2026-04-28", "2026-05-04"),
    "i-0untaggedfleet01": ("2026-03-01", "2026-05-31"),
    "ddb-table-events-prod": ("2026-04-01", "2026-05-31"),
    "i-0fbgpu00000000": ("2026-04-08", "2026-04-26"),
    "i-0fbgpu00000001": ("2026-04-08", "2026-04-26"),
    "i-0fbgpu00000002": ("2026-04-08", "2026-04-26"),
    "i-0fbgpu00000003": ("2026-04-08", "2026-04-26"),
    "i-0fbgpu00000004": ("2026-04-08", "2026-04-26"),
    "vol-0orphans-aggregate": ("2026-03-01", "2026-05-31"),
    "natgw-misconfig-spike": ("2026-05-12", "2026-05-17")
}

gt_benign = {
    "migration-egress-onetime": ("2026-03-28", "2026-03-31"),
    "i-0flashsale-autoscale": ("2026-05-23", "2026-05-27"),
    "i-0loadtest-fleet": ("2026-05-06", "2026-05-08")
}

# Programmatic lists
all_res = sorted(cur['line_item_resource_id'].dropna().unique())
gt_res = list(gt_anomalies.keys()) + list(gt_benign.keys())
remaining_res = [r for r in all_res if r not in gt_res]

# Group remaining by service
res_by_service = {}
for r in remaining_res:
    prod_code = cur[cur['line_item_resource_id'] == r]['line_item_product_code'].values[0]
    res_by_service.setdefault(prod_code, []).append(r)

synth_anomalies = []
synth_benign = []

targets = {
    'AmazonEC2': (12, 15),
    'AmazonRDS': (3, 4),
    'AmazonSageMaker': (2, 3),
    'awskms': (4, 5),
    'AmazonKinesis': (2, 3)
}

for service, resources in sorted(res_by_service.items()):
    n_anom, n_benign = targets.get(service, (0, 0))
    synth_anomalies.extend(resources[:n_anom])
    synth_benign.extend(resources[n_anom:n_anom+n_benign])

# Mapping from resource to its min and max cost
cost_stats = cur.groupby('line_item_resource_id')['line_item_unblended_cost'].agg(['min', 'max']).to_dict('index')

# RAM size mapping based on instance type
ram_sizes = {
    'm5.2xlarge': 32768, 'c5.2xlarge': 16384, 'm5.4xlarge': 65536, 'm5.xlarge': 16384,
    'm5.large': 8192, 'c5.4xlarge': 32768, 't3.large': 8192, 'p3.2xlarge': 62464,
    'db.r5.2xlarge': 65536, 'db.m5.xlarge': 16384, 'db.m5.large': 8192,
    'cache.r6g.large': 12288, 'kafka.m5.large': 8192, 'r6g.large.search': 16384
}

def get_ram_size(inst_type):
    if pd.isna(inst_type):
        return 8192
    return ram_sizes.get(inst_type, 8192)

# Set random seed for reproducibility
np.random.seed(42)

records = []
ddb_start_date = pd.to_datetime("2026-04-01")

for idx, row in cur.iterrows():
    res_id = row['line_item_resource_id']
    date_str = row['date_str']
    timestamp = row['line_item_usage_start_date'].isoformat()
    service = row['line_item_product_code']
    inst_type = row['product_instance_type']
    cost = row['line_item_unblended_cost']
    usage_amt = row['line_item_usage_amount']
    is_wknd = row['line_item_usage_start_date'].dayofweek >= 5
    
    # Hash of resource to get consistent type of anomaly/benign
    res_hash = int(hashlib.md5(res_id.encode('utf-8')).hexdigest(), 16)
    
    # 1. Determine Label
    label = 'normal'
    
    # Ground truth check
    if res_id in gt_anomalies:
        start, end = gt_anomalies[res_id]
        if start <= date_str <= end:
            label = 'anomaly'
    elif res_id in gt_benign:
        start, end = gt_benign[res_id]
        if start <= date_str <= end:
            label = 'benign'
    # Synthetic check
    elif res_id in synth_anomalies:
        if "2026-04-01" <= date_str <= "2026-05-31":
            label = 'anomaly'
    elif res_id in synth_benign:
        if "2026-04-01" <= date_str <= "2026-05-31":
            label = 'benign'
            
    # 2. Base metrics calculations (Normal Baseline)
    stats = cost_stats.get(res_id, {'min': 0.0, 'max': 10.0})
    min_c = stats['min']
    max_c = stats['max']
    
    # Calculate relative utilization factor
    if max_c - min_c > 1e-4:
        # Scale cost between 0.1 and 0.9
        u = 0.1 + 0.8 * (cost - min_c) / (max_c - min_c)
    else:
        u = np.clip(0.1 + 0.7 * (cost / 1000.0), 0.1, 0.8)
        
    u = np.clip(u, 0.05, 0.95)
    
    # Linear mapping to ensure strong correlation
    if service == 'AmazonEC2':
        cpu_percent = 15.0 + 55.0 * u + np.random.uniform(-3.0, 3.0)
    elif service == 'AmazonRDS':
        cpu_percent = 12.0 + 48.0 * u + np.random.uniform(-2.0, 2.0)
    else:
        cpu_percent = 10.0 + 40.0 * u + np.random.uniform(-2.0, 2.0)
        
    cpu_percent = np.clip(cpu_percent, 2.0, 98.0)
    
    # Diurnal hourly utilization centered around cpu_percent
    hours = np.arange(24)
    h_cycle = np.cos(2 * np.pi * (hours - 15) / 24) # cosine wave centered on 0
    amp = min(15.0, cpu_percent - 2.0, 98.0 - cpu_percent)
    cpu_util_hourly = cpu_percent + amp * h_cycle * np.random.uniform(0.95, 1.05, size=24)
    if is_wknd:
        cpu_util_hourly = cpu_util_hourly * 0.75 # drop on weekends
    cpu_util_hourly = np.clip(cpu_util_hourly, 2.0, 98.0).tolist()
    
    # Recalculate cpu_percent to be exactly the mean of hourly
    cpu_percent = float(np.mean(cpu_util_hourly))
    
    ram = get_ram_size(inst_type)
    mem_util = np.clip(0.25 + 0.45 * u + 0.1 * (cpu_percent / 100.0) + np.random.uniform(-0.02, 0.02), 0.1, 0.95)
    memory_mib = float(ram * mem_util)
    
    # Network metrics base
    if service == 'AWSDataTransfer':
        network_out_bytes = float(usage_amt * 10**9 * np.random.uniform(0.98, 1.02))
        network_in_bytes = float(usage_amt * 0.05 * 10**9 * np.random.uniform(0.98, 1.02))
    elif service == 'AWSELB':
        network_in_bytes = float(cost * 1.5 * 10**8 * np.random.uniform(0.95, 1.05))
        network_out_bytes = float(cost * 1.8 * 10**8 * np.random.uniform(0.95, 1.05))
    else:
        network_in_bytes = float(cost * 10**7 * np.random.uniform(0.95, 1.05))
        network_out_bytes = float(cost * 1.2 * 10**7 * np.random.uniform(0.95, 1.05))
        
    # Disk I/O base
    if service == 'AmazonS3':
        disk_io_ops = float(usage_amt * 500.0 * np.random.uniform(0.95, 1.05))
    else:
        disk_io_ops = float(cost * 2000.0 * np.random.uniform(0.95, 1.05))
        
    # Conditional fields
    database_connections = None
    if service == 'AmazonRDS':
        database_connections = int(np.clip(8 + 32 * u + np.random.randint(-1, 2), 2, 200))
        # Update CPU/Mem based on DB connections
        cpu_percent = np.clip(cpu_percent + 0.15 * database_connections, 2.0, 98.0)
        cpu_util_hourly = [np.clip(h + 0.15 * database_connections, 2.0, 98.0) for h in cpu_util_hourly]
        memory_mib = np.clip(memory_mib + 15.0 * database_connections, 256.0, ram * 0.95)
        
    gpu_utilization = None
    if inst_type == 'p3.2xlarge' or service == 'AmazonSageMaker':
        gpu_utilization = float(np.clip(8.0 + 20.0 * u + np.random.uniform(-0.5, 0.5), 0.0, 100.0))
        
    # 3. Apply Anomalous / Benign Modifications
    if label == 'anomaly':
        if res_id == "arn:aws:rds:us-east-1:acct:db:db-staging-orphan-01":
            database_connections = int(np.random.choice([0, 1]))
            cpu_percent = float(np.random.uniform(1.0, 2.5))
            cpu_util_hourly = np.random.uniform(1.0, 2.5, size=24).tolist()
            memory_mib = float(ram * 0.4)
        elif res_id == "log-group-debug-runaway":
            network_in_bytes = float(network_in_bytes * 15.0 * np.random.uniform(0.9, 1.1))
            disk_io_ops = float(disk_io_ops * 10.0 * np.random.uniform(0.9, 1.1))
        elif res_id == "i-0untaggedfleet01":
            cpu_percent = float(45.0 + np.random.uniform(-1.0, 1.0))
            cpu_util_hourly = np.random.uniform(42.0, 48.0, size=24).tolist()
        elif res_id == "ddb-table-events-prod":
            days_passed = (pd.to_datetime(date_str) - ddb_start_date).days
            drift_factor = 1.0 + 0.05 * days_passed
            disk_io_ops = float(disk_io_ops * drift_factor)
            network_in_bytes = float(network_in_bytes * drift_factor)
            network_out_bytes = float(network_out_bytes * drift_factor)
        elif res_id.startswith("i-0fbgpu"):
            gpu_utilization = float(92.0 + np.random.uniform(-2.0, 2.0))
            cpu_percent = float(82.0 + np.random.uniform(-3.0, 3.0))
            cpu_util_hourly = np.random.uniform(78.0, 86.0, size=24).tolist()
            memory_mib = float(ram * 0.85)
        elif res_id == "vol-0orphans-aggregate":
            disk_io_ops = float(np.random.choice([0, 1, 2]))
            cpu_percent = 0.0
            cpu_util_hourly = [0.0] * 24
            memory_mib = 0.0
        elif res_id == "natgw-misconfig-spike":
            network_in_bytes = float(network_in_bytes * 12.0 * np.random.uniform(0.9, 1.1))
            network_out_bytes = float(network_out_bytes * 12.0 * np.random.uniform(0.9, 1.1))
        else:
            anom_type = res_hash % 5
            if anom_type == 0: # CPU Spike
                cpu_percent = float(90.0 + np.random.uniform(-2.0, 2.0))
                cpu_util_hourly = np.random.uniform(85.0, 95.0, size=24).tolist()
                memory_mib = float(np.clip(memory_mib * 1.3, 256.0, ram * 0.95))
                network_in_bytes = float(network_in_bytes * 2.0)
            elif anom_type == 1: # Memory Leak
                days_passed = (pd.to_datetime(date_str) - pd.to_datetime("2026-04-01")).days
                leak_factor = min(0.95, 0.3 + 0.015 * days_passed)
                memory_mib = float(ram * leak_factor)
                cpu_percent = float(15.0 + np.random.uniform(-2.0, 2.0))
                cpu_util_hourly = np.random.uniform(12.0, 18.0, size=24).tolist()
            elif anom_type == 2: # DDoS Traffic Spike
                network_in_bytes = float(network_in_bytes * 10.0)
                network_out_bytes = float(network_out_bytes * 5.0)
                cpu_percent = float(np.clip(cpu_percent + 30.0, 2.0, 98.0))
                cpu_util_hourly = [np.clip(h + 30.0, 2.0, 98.0) for h in cpu_util_hourly]
            elif anom_type == 3: # Database Saturation
                if database_connections is not None:
                    database_connections = int(np.clip(database_connections * 6, 150, 300))
                    cpu_percent = float(np.clip(cpu_percent + 35.0, 2.0, 98.0))
                    cpu_util_hourly = [np.clip(h + 35.0, 2.0, 98.0) for h in cpu_util_hourly]
                    memory_mib = float(np.clip(memory_mib * 1.25, 256.0, ram * 0.95))
                else:
                    disk_io_ops = float(disk_io_ops * 8.0)
                    cpu_percent = float(np.clip(cpu_percent + 10.0, 2.0, 98.0))
                    cpu_util_hourly = [np.clip(h + 10.0, 2.0, 98.0) for h in cpu_util_hourly]
            elif anom_type == 4: # Disk Bottleneck
                disk_io_ops = float(disk_io_ops * 8.0)
                cpu_percent = float(np.clip(cpu_percent + 10.0, 2.0, 98.0))
                cpu_util_hourly = [np.clip(h + 10.0, 2.0, 98.0) for h in cpu_util_hourly]

    elif label == 'benign':
        if res_id == "migration-egress-onetime":
            network_out_bytes = float(network_out_bytes * 10.0 * np.random.uniform(0.9, 1.1))
        elif res_id == "i-0flashsale-autoscale":
            cpu_util_hourly = [float(70.0 + np.random.uniform(-5, 5)) if 9 <= h <= 21 else float(20.0 + np.random.uniform(-3, 3)) for h in range(24)]
            cpu_percent = float(np.mean(cpu_util_hourly))
            network_in_bytes = float(network_in_bytes * 5.0 * np.random.uniform(0.9, 1.1))
            memory_mib = float(ram * 0.6)
        elif res_id == "i-0loadtest-fleet":
            disk_io_ops = float(disk_io_ops * 5.0)
            cpu_percent = float(np.clip(cpu_percent * 2.0, 2.0, 95.0))
            cpu_util_hourly = [np.clip(h * 2.0, 2.0, 95.0) for h in cpu_util_hourly]
        else:
            benign_type = res_hash % 4
            if benign_type == 0: # Autoscaling
                cpu_util_hourly = [float(72.0 + np.random.uniform(-4, 4)) if 10 <= h <= 18 else float(22.0 + np.random.uniform(-3, 3)) for h in range(24)]
                cpu_percent = float(np.mean(cpu_util_hourly))
                network_in_bytes = float(network_in_bytes * 4.0)
                memory_mib = float(np.clip(memory_mib * 1.25, 256.0, ram * 0.95))
            elif benign_type == 1: # Batch Job
                cpu_util_hourly = [float(65.0 + np.random.uniform(-5, 5)) if 0 <= h <= 4 else float(15.0 + np.random.uniform(-2, 2)) for h in range(24)]
                cpu_percent = float(np.mean(cpu_util_hourly))
                disk_io_ops = float(disk_io_ops * 5.0)
            elif benign_type == 2: # Backup Database
                if database_connections is not None:
                    cpu_util_hourly = [float(45.0 + np.random.uniform(-5, 5)) if h == 2 else float(18.0 + np.random.uniform(-2, 2)) for h in range(24)]
                    cpu_percent = float(np.mean(cpu_util_hourly))
                    network_out_bytes = float(network_out_bytes * 8.0)
                    database_connections = int(np.clip(database_connections + 8, 2, 200))
                else:
                    cpu_util_hourly = [float(65.0 + np.random.uniform(-5, 5)) if 0 <= h <= 4 else float(15.0 + np.random.uniform(-2, 2)) for h in range(24)]
                    cpu_percent = float(np.mean(cpu_util_hourly))
                    disk_io_ops = float(disk_io_ops * 5.0)
            elif benign_type == 3: # GPU Training Job
                if gpu_utilization is not None:
                    gpu_util_hourly = [float(95.0 + np.random.uniform(-2, 2)) if 13 <= h <= 17 else float(2.0 + np.random.uniform(-1, 1)) for h in range(24)]
                    gpu_utilization = float(np.mean(gpu_util_hourly))
                    cpu_util_hourly = [float(75.0 + np.random.uniform(-4, 4)) if 13 <= h <= 17 else float(15.0 + np.random.uniform(-2, 2)) for h in range(24)]
                    cpu_percent = float(np.mean(cpu_util_hourly))
                else:
                    cpu_util_hourly = [float(65.0 + np.random.uniform(-5, 5)) if 0 <= h <= 4 else float(15.0 + np.random.uniform(-2, 2)) for h in range(24)]
                    cpu_percent = float(np.mean(cpu_util_hourly))
                    disk_io_ops = float(disk_io_ops * 5.0)

    rec = {
        "resource_id": str(res_id),
        "timestamp": str(timestamp),
        "cpu_percent": float(cpu_percent),
        "cpu_utilization_hourly": [float(x) for x in cpu_util_hourly],
        "memory_mib": float(memory_mib),
        "network_in_bytes": float(network_in_bytes),
        "network_out_bytes": float(network_out_bytes),
        "disk_io_ops": float(disk_io_ops),
        "database_connections": int(database_connections) if database_connections is not None else None,
        "gpu_utilization": float(gpu_utilization) if gpu_utilization is not None else None,
        "label": str(label)
    }
    records.append(rec)

# Ensure metrics directory exists
metrics_dir = os.path.join(data_dir, "metrics")
os.makedirs(metrics_dir, exist_ok=True)

output_path = os.path.join(metrics_dir, "resource_metrics.json")
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(records, f, indent=2, ensure_ascii=False)

print(f"Successfully generated {len(records)} records and saved to {output_path}!")
