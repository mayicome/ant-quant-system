"""
定时执行X Following推文提取程序
在每天晚上7点到早上7点之间，每小时自动执行一次数据获取
运行时间段：19:00 - 06:59（跨天）
"""

import time
from datetime import datetime, timedelta
import sys
import os

# 导入get_x_following模块
try:
    import get_x_following
except ImportError:
    print("✗ 无法导入 get_x_following 模块")
    print("请确保 get_x_following.py 文件在同一目录下")
    sys.exit(1)

# 运行时间段配置
START_HOUR = 19  # 晚上7点开始
END_HOUR = 7     # 早上7点结束（不包含）


def is_in_active_period(now=None):
    """
    检查当前时间是否在运行时间段内（19:00-06:59）
    
    参数:
        now: datetime对象，如果为None则使用当前时间
    
    返回:
        bool: 如果在运行时间段内返回True，否则返回False
    """
    if now is None:
        now = datetime.now()
    
    hour = now.hour
    # 运行时间段：19:00-23:59 和 00:00-06:59
    return hour >= START_HOUR or hour < END_HOUR


def get_next_execution_time(now=None):
    """
    计算下一次执行时间
    
    参数:
        now: datetime对象，如果为None则使用当前时间
    
    返回:
        tuple: (wait_seconds, next_time) 等待秒数和下一次执行时间
    """
    if now is None:
        now = datetime.now()
    
    # 如果当前时间在运行时间段内
    if is_in_active_period(now):
        # 计算下一个整点
        next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        
        # 如果当前时间已经是整点（分钟和秒都是0），则立即执行
        if now.minute == 0 and now.second == 0:
            return (0, now)
        
        # 检查下一个整点是否还在运行时间段内
        if is_in_active_period(next_hour):
            wait_seconds = (next_hour - now).total_seconds()
            return (wait_seconds, next_hour)
        else:
            # 下一个整点会超出时间段（比如06:59的下一个整点是07:00），等待到下一个19:00
            # 计算当天的19:00
            today_19 = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
            if now >= today_19:
                # 如果今天已经过了19:00，则等待到明天的19:00
                next_19 = today_19 + timedelta(days=1)
            else:
                # 如果今天还没到19:00，等待到今天的19:00
                next_19 = today_19
            
            wait_seconds = (next_19 - now).total_seconds()
            return (wait_seconds, next_19)
    else:
        # 当前时间不在运行时间段内（07:00-18:59），等待到当天的19:00
        today_19 = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
        wait_seconds = (today_19 - now).total_seconds()
        return (wait_seconds, today_19)


def run_extraction():
    """执行推文提取任务"""
    print("\n" + "=" * 80)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始执行定时任务")
    print("=" * 80)
    
    try:
        # 调用get_x_following的主函数
        get_x_following.main()
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 定时任务执行完成")
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✗ 定时任务执行出错: {e}")
    
    print("=" * 80 + "\n")


def schedule_task():
    """定时任务循环"""
    while True:
        # 计算下一次执行时间
        wait_time, next_time = get_next_execution_time()
        
        if wait_time > 0:
            # 显示等待信息
            current_time = datetime.now()
            hours = int(wait_time // 3600)
            minutes = int((wait_time % 3600) // 60)
            seconds = int(wait_time % 60)
            
            print(f"[{current_time.strftime('%Y-%m-%d %H:%M:%S')}] 等待到下一次执行时间: {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
            if hours > 0:
                print(f"  等待时间: {hours} 小时 {minutes} 分钟 {seconds} 秒")
            else:
                print(f"  等待时间: {minutes} 分钟 {seconds} 秒")
            
            # 等待到执行时间
            time.sleep(wait_time)
        
        # 检查当前时间是否在运行时间段内（防止在等待过程中时间已过）
        if is_in_active_period():
            # 执行任务
            run_extraction()
        else:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 当前时间不在运行时间段内，跳过执行")
        
        # 等待1秒，避免在同一秒内重复执行
        time.sleep(1)


def main():
    """主函数"""
    print("=" * 80)
    print("X Following 定时提取程序")
    print("=" * 80)
    print()
    print("运行时间段：每天晚上 19:00 - 早上 07:00（每小时执行一次）")
    print("在运行时间段内，每小时整点自动执行推文提取任务")
    print()
    print("提示：请确保Chrome浏览器已以调试模式启动")
    print("启动命令：")
    print('  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\temp\\chrome_debug"')
    print()
    
    current_time = datetime.now()
    print(f"当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 检查当前时间是否在运行时间段内
    if is_in_active_period(current_time):
        print("✓ 当前时间在运行时间段内")
        # 计算到下一个执行时间的等待时间
        wait_time, next_time = get_next_execution_time(current_time)
        
        if wait_time > 0:
            hours = int(wait_time // 3600)
            minutes = int((wait_time % 3600) // 60)
            seconds = int(wait_time % 60)
            
            print(f"下次执行时间: {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
            if hours > 0:
                print(f"等待时间: {hours} 小时 {minutes} 分钟 {seconds} 秒")
            else:
                print(f"等待时间: {minutes} 分钟 {seconds} 秒")
            print()
            print("程序正在运行，将在指定时间自动执行...")
            print("按 Ctrl+C 可以停止程序")
            print()
            
            # 如果当前时间已经是整点，立即执行一次
            if current_time.minute == 0 and current_time.second == 0:
                print("当前时间正好是整点，立即执行第一次任务")
                run_extraction()
            else:
                # 等待到下一个执行时间
                time.sleep(wait_time)
                run_extraction()
        else:
            # 立即执行
            run_extraction()
    else:
        print("⚠ 当前时间不在运行时间段内（07:00-18:59）")
        # 计算到晚上7点的等待时间
        wait_time, next_time = get_next_execution_time(current_time)
        hours = int(wait_time // 3600)
        minutes = int((wait_time % 3600) // 60)
        seconds = int(wait_time % 60)
        
        print(f"等待到运行时间段开始: {next_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if hours > 0:
            print(f"等待时间: {hours} 小时 {minutes} 分钟 {seconds} 秒")
        else:
            print(f"等待时间: {minutes} 分钟 {seconds} 秒")
        print()
        print("程序正在运行，将在运行时间段开始时自动执行...")
        print("按 Ctrl+C 可以停止程序")
        print()
        
        # 等待到运行时间段开始
        time.sleep(wait_time)
        # 运行时间段开始后立即执行一次
        run_extraction()
    
    print("定时任务已设置：在运行时间段内每小时整点执行")
    print("程序将持续运行，按 Ctrl+C 停止")
    print()
    
    # 启动定时任务循环
    try:
        schedule_task()
    except KeyboardInterrupt:
        print("\n\n程序已停止")
        print("感谢使用！")


if __name__ == "__main__":
    main()

