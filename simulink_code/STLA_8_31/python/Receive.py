import socket
import threading
import time
import struct
import matplotlib.pyplot as plt
from itertools import cycle  
import matplotlib.animation as animation
import numpy as np
import sys

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

EFCMNT_PDLE_ACCEL         = 0.0   
HV_BATT_TEMP_AVG          = 0.0   
VITESSE_VEHICULE_ROUES    = 0.0   
POS_MONOSTABLE_LEVER      = 0.0   
HV_BATT_SOC               = 0.0   
STDE_DRV_DYN_MODE_STATE   = 0.0   
STDE_DRV_ENGY_MODE_STATE  = 0.0   

accel_data          = []
temp_data_avg       = []
V_current_data      = []
gear_data           = []
soc_data            = []
dyn_mode_data       = []
engy_mode_data      = []
time_data           = []
V_target_data       = []
Engine_torque_data  = []
Engine_speed_data   = []
Motor_torque_data   = []
Motor_speed_data    = []
temp_data_max       = []
temp_data           = []
Current_data        = []
Voltage_data        = []

data_lock = threading.Lock()

def clear_all_data():
    time_data.clear()
    accel_data.clear()
    temp_data_avg.clear()
    V_current_data.clear()
    gear_data.clear()
    soc_data.clear()
    dyn_mode_data.clear()
    engy_mode_data.clear()
    V_target_data.clear()
    Engine_torque_data.clear()
    Engine_speed_data.clear()
    Motor_torque_data.clear()
    Motor_speed_data.clear()
    temp_data.clear()
    temp_data_max.clear()
    Current_data.clear()
    Voltage_data.clear()

def tcp_subscriber_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    while True:
        try:
            print("正在尝试连接后端服务 (端口 6000)...")
            sock.connect(("127.0.0.1", 6000))
            print("成功连接！开始接收全景数据。")
            break
        except ConnectionRefusedError:
            time.sleep(2)  
            
    buffer = b''
    FRAME_SIZE = 136 
    
    try:
        while True:
            packet = sock.recv(1024)
            if not packet:
                print("\n 后端已断开。")
                break
            
            buffer += packet
            while len(buffer) >= FRAME_SIZE:
                frame = buffer[:FRAME_SIZE]
                buffer = buffer[FRAME_SIZE:]
                
                unpacked = struct.unpack('<17d', frame)
                
                sim_time = unpacked[7]  
                with data_lock:
                    if len(time_data) > 0 and sim_time < time_data[-1]:
                        clear_all_data()

                    time_data.append(sim_time)
                    accel_data.append(unpacked[0])
                    temp_data_avg.append(unpacked[1])
                    V_current_data.append(unpacked[2])
                    gear_data.append(unpacked[3])
                    soc_data.append(unpacked[4])
                    dyn_mode_data.append(unpacked[5])
                    engy_mode_data.append(unpacked[6])
                    V_target_data.append(unpacked[8])
                    Engine_torque_data.append(unpacked[9])
                    Engine_speed_data.append(unpacked[10])
                    Motor_torque_data.append(unpacked[11])
                    Motor_speed_data.append(unpacked[12])
                    temp_data.append(unpacked[13])
                    temp_data_max.append(unpacked[14])
                    Current_data.append(unpacked[15])
                    Voltage_data.append(unpacked[16])
    except Exception as e:
        print(f"通讯出错: {e}")
    finally:
        sock.close()

def update_plot_1(frame, lines, axes):
    
    with data_lock:

        if len(time_data) < 2:
            return lines.values()

        t_data = time_data.copy()
        v_current = V_current_data.copy()
        v_target = V_target_data.copy()
        temp_avg = temp_data_avg.copy()
        accel = accel_data.copy()
        gear = gear_data.copy()
        soc = soc_data.copy()
        dyn_mode = dyn_mode_data.copy()
        engy_mode = engy_mode_data.copy()
        temp = temp_data.copy()
        temp_max = temp_data_max.copy()
        
    lines['V_current'].set_data(t_data, v_current)
    lines['V_target'].set_data(t_data, v_target)
    lines['temp_avg'].set_data(t_data, temp_avg)
    lines['accel'].set_data(t_data, accel)
    lines['gear'].set_data(t_data, gear)
    lines['soc'].set_data(t_data, soc)
    lines['dyn_mode'].set_data(t_data, dyn_mode)
    lines['engy_mode'].set_data(t_data, engy_mode)
    lines['temp'].set_data(t_data, temp)
    lines['temp_max'].set_data(t_data, temp_max)
    current_t = t_data[-1]
    

    for ax in axes[0:6]:

        ax.set_xlim(0, max(10, current_t * 1.05))
        ax.relim()             
        ax.autoscale_view(scalex=False, scaley=True)  

        if len(temp_max) > 0:
            real_max_temp = max(temp_max)
        else:
            real_max_temp = 25

        axes[1].set_ylim(bottom=20, top=max(25, real_max_temp + 2))

    return lines.values()

def update_plot_2(frame, lines, axes):
    
    with data_lock:

        if len(time_data) < 2:
            return lines.values()

        t_data = time_data.copy()
        engine_torque = Engine_torque_data.copy()
        engine_speed = Engine_speed_data.copy()
        motor_torque = Motor_torque_data.copy()
        motor_speed = Motor_speed_data.copy()
        Current = Current_data.copy()
        Voltage = Voltage_data.copy()
        
    lines['Engine_torque'].set_data(t_data, engine_torque)
    lines['Engine_speed'].set_data(t_data, engine_speed)
    lines['Motor_torque'].set_data(t_data, motor_torque)
    lines['Motor_speed'].set_data(t_data, motor_speed)
    lines['Current'].set_data(t_data, Current)
    lines['Voltage'].set_data(t_data, Voltage)
    current_t = t_data[-1]

    for ax in axes[6:12]:

        ax.set_xlim(0, max(10, current_t * 1.05))
        ax.relim()             
        ax.autoscale_view(scalex=False, scaley=True)  

    return lines.values()

def main():
    print("="*60)
    print(" 监测大屏已启动")
    print("="*60)

    threading.Thread(target=tcp_subscriber_thread, daemon=True).start()


    fig, axes_2d = plt.subplots(3, 2, figsize=(14, 9))
    fig.canvas.manager.set_window_title('独立界面监测系统')
    fig.subplots_adjust(hspace=0.4, wspace=0.2)
    

    axes = axes_2d.flatten()

    colors = ['#FF4500', '#FF8C00', '#1E90FF', '#8A2BE2', '#32CD32']
    color_cycle = cycle(colors)  
    lines = {}


   
    ##子图1 速度反馈
    axes[0].set_title('速度反馈', loc='left', fontsize=10)
    axes[0].set_xlabel("监控运行时间 (秒)", fontsize=9)
    axes[0].grid(True, linestyle='--', alpha=0.5)
    lines['V_current'], = axes[0].plot([], [], lw=2, color=next(color_cycle), label='实际速度')
    lines['V_target'], = axes[0].plot([], [], lw=2, color=next(color_cycle), label='目标速度')
    axes[0].legend(loc='upper right', fontsize=8.5) # 在右上角显示图例

    ##子图2 高压电池平均温度 (℃)
    axes[1].set_title('高压电池温度 (℃)', loc='left', fontsize=10)
    axes[1].set_xlabel("监控运行时间 (秒)", fontsize=9)
    axes[1].grid(True, linestyle='--', alpha=0.5)
    lines['temp'], = axes[1].plot([], [], lw=2, color=next(color_cycle), label='实时温度')
    lines['temp_avg'], = axes[1].plot([], [], lw=2, color=next(color_cycle), label='平均温度')
    lines['temp_max'], = axes[1].plot([], [], lw=2, color=next(color_cycle), label='最高温度')
    #axes[1].legend(loc='upper right', fontsize=8.5)
    axes[1].legend(loc='lower right', bbox_to_anchor=(1.0, 0.95), ncol=3, frameon=False, fontsize=7.5)

    ##子图3 油门踏板深度
    axes[2].set_title('油门踏板深度', loc='left', fontsize=10)
    axes[2].set_xlabel("监控运行时间 (秒)", fontsize=9)
    axes[2].grid(True, linestyle='--', alpha=0.5)
    lines['accel'], = axes[2].plot([], [], lw=2, color=next(color_cycle))
    

    ##子图4 当前挡位反馈
    axes[3].set_title('当前挡位反馈', loc='left', fontsize=10)
    axes[3].set_xlabel("监控运行时间 (秒)", fontsize=9)
    axes[3].grid(True, linestyle='--', alpha=0.5)
    lines['gear'], = axes[3].plot([], [], lw=2, color=next(color_cycle), drawstyle='steps-post',label='挡位:P(0) N(1) D(2~8)')
    axes[3].legend(loc='lower right', bbox_to_anchor=(1.0, 0.95), ncol=3, frameon=False, fontsize=7.5)

    ##子图5 高压电池 SOC
    axes[4].set_title('高压电池 SOC', loc='left', fontsize=10)
    axes[4].set_xlabel("监控运行时间 (秒)", fontsize=9)
    axes[4].grid(True, linestyle='--', alpha=0.5)
    lines['soc'], = axes[4].plot([], [], lw=2, color=next(color_cycle))

    ##子图6 控制模式状态机反馈
    axes[5].set_title('控制模式状态机反馈', loc='left', fontsize=10)
    axes[5].set_xlabel("监控运行时间 (秒)", fontsize=9)
    axes[5].grid(True, linestyle='--', alpha=0.5)
    lines['dyn_mode'], = axes[5].plot([], [], lw=2, color=next(color_cycle), drawstyle='steps-post',label='驾模(0:ECO 1:COM 2:SPT)')
    lines['engy_mode'], = axes[5].plot([], [], lw=2, color=next(color_cycle), drawstyle='steps-post',label='能模(0:EV 1:HEV)')
    axes[5].legend(loc='lower right', bbox_to_anchor=(1.0, 0.95), ncol=3, frameon=False, fontsize=7.5)

    # ====== 第二个ui界面 ======

    fig_power, axes_power_2d = plt.subplots(3,2,figsize=(14, 8))
    fig_power.canvas.manager.set_window_title('独立界面监测系统')
    fig_power.subplots_adjust(hspace=0.4, wspace=0.2)

    axes = np.concatenate((axes,axes_power_2d.flatten()))

    # 子图7 发动机扭矩  
    axes[6].set_title('发动机扭矩',loc='left',fontsize=10)
    axes[6].set_xlabel("监控运行时间 (秒)", fontsize=9)
    axes[6].set_ylabel("扭矩 (N·m)",fontsize=9)
    axes[6].grid(True,linestyle='--',alpha=0.5)
    lines['Engine_torque'], = axes[6].plot( [],[],lw=2,color=next(color_cycle))

    # 子图8 发动机转速
    axes[7].set_title( '发动机转速',loc='left',fontsize=10)
    axes[7].set_xlabel("监控运行时间 (秒)",fontsize=9)
    axes[7].set_ylabel("转速 (rpm)",fontsize=9)
    axes[7].grid(True,linestyle='--',alpha=0.5)
    lines['Engine_speed'], = axes[7].plot([],[],lw=2,color=next(color_cycle))

    # 子图9 电机扭矩
    axes[8].set_title('电机扭矩',loc='left',fontsize=10)
    axes[8].set_xlabel("监控运行时间 (秒)",fontsize=9)
    axes[8].set_ylabel("扭矩 (N·m)",fontsize=9)
    axes[8].grid(True,linestyle='--',alpha=0.5)
    lines['Motor_torque'], = axes[8].plot([],[],lw=2,color=next(color_cycle))

    # 子图10 电机转速
    axes[9].set_title('电机转速',loc='left',fontsize=10)
    axes[9].set_xlabel("监控运行时间 (秒)",fontsize=9)
    axes[9].set_ylabel("转速 (rpm)",fontsize=9)
    axes[9].grid(True,linestyle='--',alpha=0.5)
    lines['Motor_speed'], = axes[9].plot([],[],lw=2,color=next(color_cycle))

    # 子图11 电流
    axes[10].set_title('电流',loc='left',fontsize=10)
    axes[10].set_xlabel("监控运行时间 (秒)",fontsize=9)
    axes[10].set_ylabel("电流 (A)",fontsize=9)
    axes[10].grid(True,linestyle='--',alpha=0.5)
    lines['Current'], = axes[10].plot([],[],lw=2,color=next(color_cycle))

    # 子图10 电机转速
    axes[11].set_title('电压',loc='left',fontsize=10)
    axes[11].set_xlabel("监控运行时间 (秒)",fontsize=9)
    axes[11].set_ylabel("电压 (V)",fontsize=9)
    axes[11].grid(True,linestyle='--',alpha=0.5)
    lines['Voltage'], = axes[11].plot([],[],lw=2,color=next(color_cycle))

    ani_1 = animation.FuncAnimation(fig, update_plot_1, fargs=(lines, axes), interval=50, blit=False, cache_frame_data=False)
    ani_2 = animation.FuncAnimation(fig_power,update_plot_2,fargs=(lines,axes),interval=50,blit=False,cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n" + "="*60)
        print(" 退出监测系统...")
        print("="*60)
        sys.exit(0) 
