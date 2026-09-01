
'''
 rms 测试代码
 使用办法： 只需将底部文件路径修改即可运行

'''

import pandas as pd
import os
import glob
import warnings
import portrait_core as pc
warnings.filterwarnings("ignore", category=FutureWarning,
                        message=".*ChainedAssignmentError.*")

model = pc.VehiclePortraitModel()

def analyze(path, platform='BEV', capacity=60):
    # df = pd.read_parquet(path)
    df = pd.read_csv(path)
    df['ts'] = pd.to_datetime(df['ts'])

    r = model.predict(df, platform=platform, battery_capacity_kwh=capacity)
    if r['meta']['status'] != 'ok':
        return {'ok': False, 'code': r['meta']['abnormal_code'],
                'reason': r['meta']['abnormal_reason']}

    s, sc = r['driving_style_profile'], r['driving_scenario_profile']
    return {
        'ok': True,
        'style': s['driving_style'],
        'score': s['aggressiveness_score'],
        'confidence': s['style_confidence'],
        'scenario': sc['driving_scenario'],
        'congestion': sc['congestion_level'],
        'distance_km': sc['window_distance_km'],
        'quality': r['meta']['data_quality_score'],
        'warnings': r['meta']['warnings'],
    }



# res = analyze(path=r'D:\MyPython\DLLM_VCU_V3.2\VCU_motor_code\VCU_motor_code\RMS\segments_2000\2000_PHEV_ABNORMAL_DISTRIBUTION_OUTLIER.csv')
res = analyze(path=r'C:\Users\yyf\Desktop\DLLM_VCU_V3.3\DLLM_VCU_V3.3\DLLM_VCU_V3.3\VCU_motor_code\VCU_motor_code\RMS\segments_2000\0001_BEV_highway_cruise_normal.csv')
print(res)


