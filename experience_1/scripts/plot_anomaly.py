import os
import sys
import pandas as pd
import numpy as np

# Set stdout/stderr encoding to UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def generate_plots():
    print("=== BẮT ĐẦU VẼ BIỂU ĐỒ TRỰC QUAN HÓA DỮ LIỆU ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    joined_data_path = os.path.join(workspace_dir, "experience_1", "data", "joined", "joined_ec2_metrics.csv")
    output_image_path = os.path.join(workspace_dir, "experience_1", "plots", "ec2_anomaly_visualization.png")
    
    # Tạo thư mục plots nếu chưa có
    plots_dir = os.path.dirname(output_image_path)
    if not os.path.exists(plots_dir):
        os.makedirs(plots_dir)
        print(f"Đã tạo thư mục plots: {plots_dir}")
        
    if not os.path.exists(joined_data_path):
        print(f"[ERROR] Không tìm thấy file dữ liệu đã join tại: {joined_data_path}")
        return

    df = pd.read_csv(joined_data_path, parse_dates=["timestamp"])
    
    target_resource = "i-0000000003e9"
    df_res = df[df["resource_id"] == target_resource].sort_values(by="timestamp").copy()
    
    if len(df_res) == 0:
        print(f"[ERROR] Không tìm thấy dữ liệu của tài nguyên {target_resource} trong file.")
        return
        
    print(f"Đã lọc dữ liệu cho tài nguyên: {target_resource} ({len(df_res)} ngày dữ liệu)")

    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        return

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    fig.suptitle(f"Trực Quan Hóa Dữ Liệu Phát Hiện Dị Thường - Resource: {target_resource}", fontsize=16, fontweight='bold', y=0.95)

    dates = df_res["timestamp"]

    ax1.plot(dates, df_res["cpu_percent"], color="blue", linewidth=2, label="CPU (%)")
    ax1.set_ylabel("CPU Utilization (%)", fontsize=11, fontweight='bold')
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.set_ylim(0, 105)

    net_in_gb = df_res["network_in_bytes"] / (1024**3)
    net_out_gb = df_res["network_out_bytes"] / (1024**3)
    ax2.plot(dates, net_in_gb, color="purple", linewidth=1.5, label="Network In (GB)")
    ax2.plot(dates, net_out_gb, color="orange", linewidth=1.5, linestyle="--", label="Network Out (GB)")
    ax2.set_ylabel("Network Traffic (GB)", fontsize=11, fontweight='bold')
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="upper left")

    ax3.plot(dates, df_res["cost"], color="green", linewidth=2, label="Daily Cost ($)")
    ax3.set_ylabel("Daily Cost ($)", fontsize=11, fontweight='bold')
    ax3.set_xlabel("Thời Gian", fontsize=11, fontweight='bold')
    ax3.grid(True, linestyle="--", alpha=0.5)
    ax3.set_ylim(0, max(df_res["cost"].max() * 1.2, 40))

    # Highlight anomaly & benign
    for i in range(len(df_res) - 1):
        t1 = df_res.iloc[i]["timestamp"]
        t2 = df_res.iloc[i+1]["timestamp"]
        label = df_res.iloc[i]["label"]
        
        if label == "anomaly":
            ax1.axvspan(t1, t2, color="red", alpha=0.2)
            ax2.axvspan(t1, t2, color="red", alpha=0.2)
            ax3.axvspan(t1, t2, color="red", alpha=0.2)
        elif label == "benign":
            ax1.axvspan(t1, t2, color="green", alpha=0.15)
            ax2.axvspan(t1, t2, color="green", alpha=0.15)
            ax3.axvspan(t1, t2, color="green", alpha=0.15)

    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='red', edgecolor='red', alpha=0.2, label='Anomaly (Dị thường thực sự)'),
        Patch(facecolor='green', edgecolor='green', alpha=0.15, label='Benign (Tải cao hợp lệ - Bẫy FP)'),
        Patch(facecolor='white', edgecolor='blue', alpha=0.6, label='Normal (Bình thường)')
    ]
    ax1.legend(handles=legend_elements, loc="upper left")

    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.gca().xaxis.set_major_locator(mdates.DayLocator(interval=5))
    plt.gcf().autofmt_xdate()

    plt.tight_layout()
    plt.subplots_adjust(top=0.88)
    
    plt.savefig(output_image_path, dpi=150)
    plt.close()
    
    print(f"=== VẼ BIỂU ĐỒ THÀNH CÔNG ===")
    print(f"Đồ thị trực quan đã được lưu tại: {output_image_path}")

    # Copy to artifacts directory if workspace path exists
    # C:\Users\narut\.gemini\antigravity-ide\brain\b140f2d5-1cce-4db2-b385-18854872634b
    artifact_dir = r"C:\Users\narut\.gemini\antigravity-ide\brain\b140f2d5-1cce-4db2-b385-18854872634b"
    if os.path.exists(artifact_dir):
        import shutil
        shutil.copy(output_image_path, os.path.join(artifact_dir, "ec2_anomaly_visualization.png"))
        print("  - Đã sao chép biểu đồ vào thư mục Artifacts.")

if __name__ == "__main__":
    generate_plots()
