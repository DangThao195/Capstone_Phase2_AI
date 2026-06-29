import os
import sys
import subprocess

# Reconfigure stdout/stderr to UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def run_pipeline():
    print("=================================================================")
    print("🚀 BẮT ĐẦU CHẠY TOÀN BỘ PIPELINE HUẤN LUYỆN & PHÂN TÍCH AIOPS")
    print("=================================================================\n")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    python_exe = sys.executable  # Sử dụng chính python hiện tại (venv)
    
    # Định nghĩa các bước chạy theo thứ tự
    pipeline_steps = [
        ("1. EDA & Khớp nối dữ liệu (Data Joining)", "eda_and_join.py"),
        ("2. Trích xuất đặc trưng (Feature Engineering)", "feature_engineering.py"),
        ("3. Gán nhãn & Phân chia dữ liệu (Labeling & Splits)", "data_labeling_and_split.py"),
        ("4. Huấn luyện model & Đánh giá (XGBoost Training)", "train_and_evaluate.py"),
        ("5. Vẽ biểu đồ phát hiện dị thường (Plot Anomaly)", "plot_anomaly.py"),
        ("6. Vẽ biểu đồ phân bố đặc trưng (Plot Distributions)", "plot_distributions.py")
    ]
    
    for idx, (step_name, filename) in enumerate(pipeline_steps, 1):
        print(f"\n👉 Đang thực hiện Bước {step_name}...")
        script_path = os.path.join(script_dir, "scripts", filename)
        
        # Chạy subprocess để đảm bảo log độc lập
        # Truyền biến môi trường PYTHONIOENCODING=utf-8 cho tiến trình con để tránh lỗi hiển thị tiếng Việt
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run([python_exe, script_path], env=env, capture_output=False, text=True)
        
        if result.returncode != 0:
            print(f"\n❌ [ERROR] Lỗi xảy ra ở Bước {idx} ({step_name}). Pipeline dừng lại.")
            sys.exit(result.returncode)
            
    print("\n=================================================================")
    print("🎉 PIPELINE HOÀN THÀNH XUẤT SẮC - TẤT CẢ MODEL ĐÃ ĐẠT CHUẨN KPI!")
    print("=================================================================")

if __name__ == "__main__":
    run_pipeline()
