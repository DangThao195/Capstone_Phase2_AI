import os
import sys
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# Reconfigure stdout to use UTF-8 to avoid Windows CP1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def generate_eda_plots():
    print("=== BẮT ĐẦU VẼ BIỂU ĐỒ EDA ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    joined_file = os.path.join(workspace_dir, "experience_2", "data", "joined", "joined_metrics_all.csv")
    output_dir = os.path.join(workspace_dir, "experience_2", "plots")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Đã tạo thư mục lưu plots: {output_dir}")
        
    if not os.path.exists(joined_file):
        print(f"[ERROR] Không tìm thấy file dữ liệu: {joined_file}")
        return
        
    df = pd.read_csv(joined_file, parse_dates=["timestamp"])
    print(f"Loaded dataset for plotting. Shape: {df.shape}")
    
    # Thiết lập giao diện biểu đồ đẹp mắt (Premium Dark-ish theme / Seaborn)
    sns.set_theme(style="whitegrid")
    plt.rcParams['font.family'] = 'DejaVu Sans'
    
    # -------------------------------------------------------------
    # Plot 1: Phân bố nhãn (Label Distribution)
    # -------------------------------------------------------------
    plt.figure(figsize=(8, 5))
    colors = {"normal": "#2b5c8f", "benign": "#2ca02c", "anomaly": "#d62728"}
    ax = sns.countplot(data=df, x="label", palette=colors, order=["normal", "benign", "anomaly"])
    plt.title("Phân Bố Nhãn Dữ Liệu (Label Distribution)", fontsize=14, fontweight="bold", pad=15)
    plt.xlabel("Nhãn (Label)", fontsize=12)
    plt.ylabel("Số lượng dòng (Hourly Rows)", fontsize=12)
    
    # Thêm số liệu lên đầu cột
    for p in ax.patches:
        height = p.get_height()
        ax.annotate(f'{height:,}\n({height/len(df)*100:.1f}%)',
                    (p.get_x() + p.get_width() / 2., height),
                    ha='center', va='bottom', fontsize=10, fontweight="bold", xytext=(0, 5),
                    textcoords='offset points')
                    
    plt.tight_layout()
    plot1_path = os.path.join(output_dir, "label_distribution.png")
    plt.savefig(plot1_path, dpi=150)
    plt.close()
    print(f"  * Đã lưu plot phân bố nhãn tại: {plot1_path}")
    
    # -------------------------------------------------------------
    # Plot 2: Phân bố Resource Type
    # -------------------------------------------------------------
    plt.figure(figsize=(10, 6))
    ax = sns.countplot(
        data=df, 
        y="resource_type", 
        order=df["resource_type"].value_counts().index,
        palette="viridis"
    )
    plt.title("Phân Bố Loại Tài Nguyên (Resource Type Distribution)", fontsize=14, fontweight="bold", pad=15)
    plt.ylabel("Loại tài nguyên", fontsize=12)
    plt.xlabel("Số lượng dòng (Hourly Rows)", fontsize=12)
    
    # Thêm số liệu lên đầu cột
    for p in ax.patches:
        width = p.get_width()
        ax.annotate(f'{width:,} ({width/len(df)*100:.1f}%)',
                    (width, p.get_y() + p.get_height() / 2.),
                    ha='left', va='center', fontsize=10, fontweight="bold", xytext=(8, 0),
                    textcoords='offset points')
                    
    plt.tight_layout()
    plot2_path = os.path.join(output_dir, "resource_type_distribution.png")
    plt.savefig(plot2_path, dpi=150)
    plt.close()
    print(f"  * Đã lưu plot phân bố tài nguyên tại: {plot2_path}")
    
    # -------------------------------------------------------------
    # Plot 3: Chuỗi thời gian của 1 resource mẫu biểu thị Anomaly & Benign
    # Chọn một resource loại compute có chứa cả anomaly và benign để vẽ
    # -------------------------------------------------------------
    compute_df = df[df["resource_type"] == "compute"]
    # Tìm resource_id có nhiều nhãn anomaly và benign nhất để vẽ mẫu
    grouped = compute_df.groupby("resource_id")["label"].value_counts().unstack().fillna(0)
    if "anomaly" in grouped.columns and "benign" in grouped.columns:
        grouped["total_events"] = grouped["anomaly"] + grouped["benign"]
        best_resource = grouped.sort_values(by="total_events", ascending=False).index[0]
    else:
        best_resource = compute_df["resource_id"].iloc[0]
        
    print(f"  - Chọn resource vẽ mẫu: {best_resource}")
    df_res = df[df["resource_id"] == best_resource].sort_values(by="timestamp").reset_index(drop=True)
    
    # Vẽ biểu đồ kết hợp CPU và Cost
    fig, ax1 = plt.subplots(figsize=(15, 6))
    
    # Trục 1: Cost (Daily)
    ax1.plot(df_res["timestamp"], df_res["cost"], color="#9467bd", alpha=0.5, label="Cost Daily ($)", linewidth=1.5)
    ax1.set_xlabel("Thời gian (Timestamp)", fontsize=12)
    ax1.set_ylabel("Chi phí hàng ngày (Cost USD)", color="#9467bd", fontsize=12)
    ax1.tick_params(axis='y', labelcolor="#9467bd")
    
    # Trục 2: CPU Percent (Hourly)
    ax2 = ax1.twinx()
    ax2.plot(df_res["timestamp"], df_res["cpu_percent"], color="#1f77b4", alpha=0.7, label="CPU (%)", linewidth=1)
    ax2.set_ylabel("CPU (%)", color="#1f77b4", fontsize=12)
    ax2.tick_params(axis='y', labelcolor="#1f77b4")
    
    # Đánh dấu các điểm Anomaly & Benign
    anom_points = df_res[df_res["label"] == "anomaly"]
    benign_points = df_res[df_res["label"] == "benign"]
    
    ax1.scatter(anom_points["timestamp"], anom_points["cost"], color="#d62728", s=60, label="Anomaly (Bất thường)", zorder=5)
    ax1.scatter(benign_points["timestamp"], benign_points["cost"], color="#2ca02c", s=40, label="Benign (Sự kiện hợp lệ)", zorder=4)
    
    # Ghép legends của cả 2 trục
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=10)
    
    plt.title(f"Trực Quan Hóa CPU & Cost của Resource Mẫu: {best_resource}", fontsize=14, fontweight="bold", pad=15)
    plt.tight_layout()
    
    plot3_path = os.path.join(output_dir, "sample_resource_timeseries.png")
    plt.savefig(plot3_path, dpi=150)
    plt.close()
    print(f"  * Đã lưu plot chuỗi thời gian mẫu tại: {plot3_path}")
    print("=== HOÀN THÀNH VẼ BIỂU ĐỒ EDA ===")

if __name__ == "__main__":
    generate_eda_plots()
