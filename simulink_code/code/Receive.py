import socket
import threading
import time
import struct
import matplotlib.pyplot as plt
import matplotlib.animation as animation

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False



time_data      = []
accel_data     = []
temp_data      = []
speed_data     = []
gear_data      = []
soc_data       = []
dyn_mode_data  = []
engy_mode_data = []
batt_temp_max_data = []
batt_curr_data     = []
batt_volt_data     = []

def clear_all_data():
    time_data.clear()
    accel_data.clear()
    temp_data.clear()
    speed_data.clear()
    gear_data.clear()
    soc_data.clear()
    dyn_mode_data.clear()
    engy_mode_data.clear()
    batt_temp_max_data.clear()
    batt_curr_data.clear()
    batt_volt_data.clear()

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
                if len(time_data) > 0 and sim_time < time_data[-1]:
                    clear_all_data()

                time_data.append(sim_time)
                accel_data.append(unpacked[0])
                temp_data.append(unpacked[1])
                speed_data.append(unpacked[2])
                gear_data.append(unpacked[3])
                soc_data.append(unpacked[4])
                dyn_mode_data.append(unpacked[5])
                engy_mode_data.append(unpacked[6])
                batt_temp_max_data.append(unpacked[14])
                batt_curr_data.append(unpacked[15])
                batt_volt_data.append(unpacked[16])
    except Exception as e:
        print(f"通讯出错: {e}")
    finally:
        sock.close()


def update_plot(frame, lines, axes):
    if len(time_data) < 2: 
        for line in lines:
            line.set_data([], [])
        return lines
        
    lines[0].set_data(time_data, accel_data)
    lines[1].set_data(time_data, temp_data)
    lines[2].set_data(time_data, speed_data)
    lines[3].set_data(time_data, gear_data)
    lines[4].set_data(time_data, soc_data)
    lines[5].set_data(time_data, dyn_mode_data)
    lines[6].set_data(time_data, engy_mode_data)
    lines[7].set_data(time_data, batt_temp_max_data)
    lines[8].set_data(time_data, batt_curr_data)
    lines[9].set_data(time_data, batt_volt_data)

    current_t = time_data[-1]
    

    for ax in axes:

        ax.set_xlim(0, max(10, current_t * 1.05))
        ax.relim()             
        ax.autoscale_view(scalex=False, scaley=True)  

    return lines

def main():
    print("="*60)
    print(" 监测大屏已启动")
    print("="*60)

    threading.Thread(target=tcp_subscriber_thread, daemon=True).start()


    fig, axes_2d = plt.subplots(3, 4, figsize=(20, 10))
    fig.canvas.manager.set_window_title('独立界面监测系统')
    fig.subplots_adjust(hspace=0.4, wspace=0.2)
    

    axes = axes_2d.flatten()

    colors = ['#FF4500', '#FF8C00', '#1E90FF', '#8A2BE2', '#32CD32',
              '#DC143C', '#008080', '#FF00FF', '#00CED1', '#A52A2A']
    titles = ['油门踏板深度', '高压电池平均温度 (℃)', '实时车速 (km/h)', '当前挡位反馈',
              '高压电池 SOC', '控制模式状态机反馈', '电池最高温度 (℃)',
              '电池电流 (A)', '电池电压 (V)', '']
    lines = []


    for i in range(5):
        draw_style = 'steps-post' if i == 3 else 'default' 
        if i == 3:
            line, = axes[i].plot([], [], lw=2, color=colors[i], drawstyle=draw_style, label='挡位 (0:P 1:N 2:R 3:D)')
            axes[i].legend(loc='upper left', fontsize=8.5) # 在左上角显示图例
        else:
            line, = axes[i].plot([], [], lw=2, color=colors[i], drawstyle=draw_style)
        lines.append(line)
        axes[i].set_title(titles[i], loc='left', fontsize=10, fontweight='bold')
        axes[i].grid(True, linestyle='--', alpha=0.5)
        axes[i].set_xlabel("监控运行时间 (秒)", fontsize=9)

    axes[5].set_title('控制模式状态机反馈', loc='left', fontsize=10, fontweight='bold')
    axes[5].grid(True, linestyle='--', alpha=0.5)
    line_dyn, = axes[5].plot([], [], lw=2.5, color='#DC143C', drawstyle='steps-post', label='驾模(0:ECO 1:COM 2:SPT)')
    line_engy, = axes[5].plot([], [], lw=2.5, color='#008080', drawstyle='steps-post', label='能模(0:EV 1:HEV)')
    lines.append(line_dyn)
    lines.append(line_engy)
    axes[5].legend(loc='upper right', fontsize=8.5)
    axes[5].set_xlabel("监控运行时间 (秒)", fontsize=9)

    # 新增三个真实通道曲线（位置 7/8/9）
    for i in (7, 8, 9):
        line, = axes[i].plot([], [], lw=2, color=colors[i])
        lines.append(line)
        axes[i].set_title(titles[i], loc='left', fontsize=10, fontweight='bold')
        axes[i].grid(True, linestyle='--', alpha=0.5)
        axes[i].set_xlabel("监控运行时间 (秒)", fontsize=9)

    # 第 10 个位置留空
    axes[10].axis('off')

    ani = animation.FuncAnimation(fig, update_plot, fargs=(lines, axes), interval=50, blit=False, cache_frame_data=False)
    plt.show()

if __name__ == "__main__":
    main()
