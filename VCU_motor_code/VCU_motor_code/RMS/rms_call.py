import pandas as pd
import os
import glob
import warnings
import portrait_core as pc
warnings.filterwarnings("ignore", category=FutureWarning,
                        message=".*ChainedAssignmentError.*")

model = pc.VehiclePortraitModel()

def analyze_df(df, platform='BEV', capacity=60):
    """对内存中的窗口 DataFrame 直接跑 RMS 驾驶画像（不依赖落盘 CSV）。"""
    df = df.copy()
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


def analyze(path, platform='BEV', capacity=60):
    """向后兼容：从 CSV 文件读取再跑画像（供 test 脚本 / 离线调试使用）。"""
    # df = pd.read_parquet(path)
    df = pd.read_csv(path)
    return analyze_df(df, platform=platform, capacity=capacity)



if __name__ == '__main__':
    # res = analyze(path=r'D:\MyPython\DLLM_VCU_V3.2\VCU_motor_code\VCU_motor_code\RMS\segments_2000\test01.csv')
    res = analyze(path=r'D:\MyPython\DLLM_VCU_V3.2\VCU_motor_code\VCU_motor_code\RMS\captured\rms_window_seg001_20260828_134428.csv')
    print(res)


