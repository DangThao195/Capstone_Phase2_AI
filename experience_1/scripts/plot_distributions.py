import os
import sys
import pandas as pd
import numpy as np

# Set stdout/stderr encoding to UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def plot_distributions():
    print("=== BẮT ĐẦU VẼ BIỂU ĐỒ PHÂN BỐ ĐẶC TRƯNG (FEATURE DISTRIBUTIONS) ===")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(os.path.dirname(script_dir))
    rds_features_path = os.path.join(workspace_dir, "experience_1", "data", "engineered", "engineered_rds_features.csv")
    ec2_features_path = os.path.join(workspace_dir, "experience_1", "data", "engineered", "engineered_ec2_features.csv")
    output_image_path = os.path.join(workspace_dir, "experience_1", "plots", "feature_distributions.png")
    
    # Tạo thư mục plots nếu chưa có
    plots_dir = os.path.dirname(output_image_path)
    if not os.path.exists(plots_dir):
        os.makedirs(plots_dir)
        print(f"Đã tạo thư mục plots: {plots_dir}")

    if not os.path.exists(ec2_features_path):
        print(f"[ERROR] Không tìm thấy dữ liệu đã feature engineering tại: {ec2_features_path}")
        return

    df_ec2 = pd.read_csv(ec2_features_path)
    df_rds = pd.read_csv(rds_features_path)
    print("Đã load thành công dữ liệu đặc trưng EC2 và RDS.")

    try:
        import matplotlib.pyplot as plt
        import seaborn as sns
    except ImportError:
        sns = None

    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 11))
    fig.suptitle("Phân Bố Các Đặc Trưng Sau Khi Feature Engineering", fontsize=16, fontweight='bold', y=0.96)

    # 1. Phân Bố CPU (%) theo Nhãn
    ax1 = axes[0, 0]
    labels_order = ["normal", "benign", "anomaly"]
    if sns is not None:
        sns.boxplot(x="label", y="cpu_percent", data=df_ec2, ax=ax1, order=labels_order, palette="Set2")
    else:
        boxplot_data = [df_ec2[df_ec2["label"] == lbl]["cpu_percent"].dropna() for lbl in labels_order]
        ax1.boxplot(boxplot_data, tick_labels=labels_order)
    ax1.set_title("1. Phân Bố CPU (%) theo Nhãn (EC2)", fontsize=12, fontweight='bold')
    ax1.set_ylabel("CPU Utilization (%)")
    ax1.set_xlabel("Nhãn Ground-Truth")
    ax1.grid(True, linestyle="--", alpha=0.3)

    # 2. So Sánh Phân Phối Cost Gốc vs Cost Scaled
    ax2 = axes[0, 1]
    if sns is not None:
        sns.kdeplot(df_rds["cost"], ax=ax2, fill=True, color="blue", label="Cost Gốc ($)", alpha=0.5)
        ax2_twin = ax2.twiny()
        sns.kdeplot(df_rds["cost_scaled"], ax=ax2_twin, fill=True, color="green", label="Cost Scaled (Robust)", alpha=0.3)
        ax2.set_xlabel("Chi Phí Gốc ($)", color="blue")
        ax2_twin.set_xlabel("Chi Phí Scaled (Robust Scale)", color="green")
        ax2.set_title("2. So Sánh Phân Phối Cost Gốc vs Cost Scaled (RDS)", fontsize=12, fontweight='bold')
    else:
        ax2.hist(df_rds["cost"].dropna(), bins=30, alpha=0.5, color="blue", label="Cost Gốc ($)")
        ax2.set_title("2. Phân phối Chi phí Gốc (RDS)", fontsize=12, fontweight='bold')
        ax2.set_xlabel("Chi phí ($)")
    ax2.grid(True, linestyle="--", alpha=0.3)

    # 3. Tỷ số lệch CPU (Deviation Ratio 7 ngày)
    ax3 = axes[1, 0]
    if sns is not None:
        sns.violinplot(x="label", y="cpu_deviation_ratio_7d", data=df_ec2, ax=ax3, order=labels_order, palette="Set3")
    else:
        violin_data = [df_ec2[df_ec2["label"] == lbl]["cpu_deviation_ratio_7d"].dropna() for lbl in labels_order]
        ax3.violinplot(violin_data)
        ax3.set_xticks([1, 2, 3])
        ax3.set_xticklabels(labels_order)
    ax3.axhline(y=3.0, color="red", linestyle="--", alpha=0.8, label="Ngưỡng động > 3")
    ax3.set_title("3. Tỷ số lệch CPU (Deviation Ratio 7 ngày) (EC2)", fontsize=12, fontweight='bold')
    ax3.set_ylabel("CPU Deviation Ratio (Số lần MAD)")
    ax3.set_xlabel("Nhãn Ground-Truth")
    ax3.grid(True, linestyle="--", alpha=0.3)

    # 4. Chỉ số hiệu năng trên Chi phí (CPU / Dollar Ratio)
    ax4 = axes[1, 1]
    if sns is not None:
        sns.boxplot(x="label", y="cpu_per_dollar", data=df_ec2, ax=ax4, order=labels_order, palette="pastel")
        ax4.set_yscale('log')
    else:
        boxplot_data_hybrid = [df_ec2[df_ec2["label"] == lbl]["cpu_per_dollar"].dropna() for lbl in labels_order]
        ax4.boxplot(boxplot_data_hybrid, tick_labels=labels_order)
        ax4.set_yscale('log')
    ax4.set_title("4. Chỉ số hiệu năng trên Chi phí (CPU/Dollar - log scale)", fontsize=12, fontweight='bold')
    ax4.set_ylabel("CPU / Dollar Ratio (Log scale)")
    ax4.set_xlabel("Nhãn Ground-Truth")
    ax4.grid(True, linestyle="--", alpha=0.3)

    plt.tight_layout()
    plt.subplots_adjust(top=0.88)
    
    plt.savefig(output_image_path, dpi=150)
    plt.close()
    
    print("=== VẼ BIỂU ĐỒ PHÂN BỐ HOÀN THÀNH THÀNH CÔNG ===")
    print(f"Đồ thị phân bố đã được lưu tại: {output_image_path}")

    # Copy to artifacts directory if workspace path exists
    artifact_dir = r"C:\Users\narut\.gemini\antigravity-ide\brain\b140f2d5-1cce-4db2-b385-18854872634b"
    if os.path.exists(artifact_dir):
        import shutil
        shutil.copy(output_image_path, os.path.join(artifact_dir, "feature_distributions.png"))
        print("  - Đã sao chép biểu đồ vào thư mục Artifacts.")

if __name__ == "__main__":
    plot_distributions()
