import os
import glob
import xgboost as xgb
import json

def inspect():
    mlruns_path = os.path.dirname(os.path.abspath(__file__)) + "/mlruns"
    model_paths = glob.glob(f"{mlruns_path}/**/model.ubj", recursive=True)
    
    print(f"Found {len(model_paths)} models in mlruns.")
    
    for path in model_paths:
        # Extract experiment and model ID from path
        parts = path.replace("\\", "/").split("/")
        # e.g., ['...', 'data', 'mlruns', '1', 'models', 'm-xxx', 'artifacts', 'model.ubj']
        exp_id = parts[-5]
        model_id = parts[-3]
        
        # Load XGBoost model
        bst = xgb.Booster()
        try:
            bst.load_model(path)
            # Get some booster attributes
            config = json.loads(bst.save_config())
            learner = config.get("learner", {})
            gradient_booster = learner.get("gradient_booster", {})
            tree_train_param = gradient_booster.get("tree_train_param", {})
            
            max_depth = tree_train_param.get("max_depth", "N/A")
            num_features = int(learner.get("num_features", 0))
            
            # Check if feature names are saved
            feature_names = bst.feature_names
            
            print(f"Exp {exp_id} | Model {model_id}:")
            print(f"  Max Depth   : {max_depth}")
            print(f"  Num Features: {num_features}")
            if feature_names:
                print(f"  Features    : {', '.join(feature_names[:5])} ... ({len(feature_names)} total)")
            else:
                print(f"  Features    : No feature names saved in booster")
        except Exception as e:
            print(f"Exp {exp_id} | Model {model_id} failed to load: {e}")

if __name__ == '__main__':
    inspect()
