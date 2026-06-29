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
    print("🚀 BẮT ĐẦU CHẠY TOÀN BỘ PIPELINE HUẤN LUYỆN & PHÂN TÍCH EXPERIENCE 2")
    print("=================================================================\n")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    python_exe = sys.executable  # Sử dụng chính python hiện tại (venv)
    
    pipeline_steps = [
        ("1. EDA & Khớp nối dữ liệu (Data Joining)", "eda_and_join.py"),
        ("2. Trích xuất đặc trưng (Feature Engineering)", "feature_engineering.py"),
        ("3. Phân tách dữ liệu (Data Splitting)", "data_split.py"),
        ("4. Huấn luyện model & Đánh giá (XGBoost Training)", "train_and_evaluate.py"),
        ("5. Vẽ biểu đồ EDA trực quan hóa", "plot_eda.py")
    ]
    
    for idx, (step_name, filename) in enumerate(pipeline_steps, 1):
        print(f"\n👉 Đang thực hiện Bước {idx}: {step_name}...")
        script_path = os.path.join(script_dir, "scripts", filename)
        
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run([python_exe, script_path], env=env)
        
        if result.returncode != 0:
            print(f"\n❌ [ERROR] Lỗi xảy ra ở Bước {idx} ({step_name}). Pipeline dừng lại.")
            sys.exit(result.returncode)
            
    print("\n=================================================================")
    print("🎉 PIPELINE EXPERIENCE 2 HOÀN THÀNH XUẤT SẮC!")
    print("=================================================================")

if __name__ == "__main__":
    run_pipeline()
