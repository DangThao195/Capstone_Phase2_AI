import pandas as pd
import os
import sys

# Reconfigure stdout for UTF-8
sys.stdout.reconfigure(encoding='utf-8')

from improvements import load_data

def check():
    df, _ = load_data()
    print("Các product codes trong dataset:")
    print(df['line_item_product_code'].value_counts())
    
    print("\nSố lượng resource unique cho mỗi product code:")
    for code, gp in df.groupby('line_item_product_code'):
        print(f"  - {code:<25}: {gp['line_item_resource_id'].nunique()} resources")
        
    print("\nChi tiết các resource cho product code khác AmazonEC2:")
    non_ec2 = df[df['line_item_product_code'] != 'AmazonEC2']
    for code, gp in non_ec2.groupby('line_item_product_code'):
        print(f"  * {code}:")
        for res in gp['line_item_resource_id'].unique():
            print(f"    - {res} ({len(gp[gp['line_item_resource_id'] == res])} bản ghi)")

if __name__ == "__main__":
    check()
