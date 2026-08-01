"""
CSV文件合并工具
功能：
1. 选择目录下的CSV文件（可指定或选择所有）
2. 合并文件
3. 按账号名称排序
4. 去重：相同账号+相同推文内容，只保留发推时间最早的一条
5. 按发推时间排序
6. 导出新的CSV文件
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict


class CSVMergerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CSV文件合并工具")
        self.root.geometry("800x600")
        
        # 获取脚本所在目录作为默认目录（使用多种方法确保可靠性）
        if getattr(sys, 'frozen', False):
            # 如果是打包后的exe文件
            script_dir = os.path.dirname(sys.executable)
        else:
            # 如果是脚本文件
            script_path = os.path.abspath(__file__)
            script_dir = os.path.dirname(script_path)
        
        # 规范化路径
        script_dir = os.path.normpath(script_dir)
        
        # 确保目录存在，如果不存在则使用当前工作目录
        if not os.path.exists(script_dir):
            script_dir = os.path.normpath(os.getcwd())
        
        # 保存脚本所在目录（用于查找x_images等）
        self.script_dir = script_dir
        
        # 如果当前目录下存在 history_data 子目录，则使用它作为默认目录
        history_data_dir = os.path.join(script_dir, 'history_data')
        if os.path.exists(history_data_dir) and os.path.isdir(history_data_dir):
            self.default_dir = os.path.normpath(history_data_dir)
        else:
            self.default_dir = script_dir
        
        # 数据存储
        self.selected_dir = tk.StringVar()
        self.merged_data = []  # 合并后的数据
        self.filtered_data = []  # 过滤后的数据（起始时间之后的记录）
        
        # 设置默认起始时间：前一天的晚上6点
        yesterday = datetime.now() - timedelta(days=1)
        default_start_time = yesterday.replace(hour=18, minute=0, second=0, microsecond=0)
        self.start_time = tk.StringVar(value=default_start_time.strftime("%Y-%m-%d %H:%M:%S"))
        
        self.create_widgets()
        
        # 自动设置默认目录并列出CSV文件
        if os.path.exists(self.default_dir):
            self.selected_dir.set(self.default_dir)
            self.log(f"默认目录: {self.default_dir}")
            # 自动列出目录下的所有CSV文件
            self.list_csv_files(self.default_dir)
        else:
            self.log(f"警告：默认目录不存在: {self.default_dir}")
    
    def create_widgets(self):
        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 目录选择区域
        dir_frame = ttk.LabelFrame(main_frame, text="选择目录", padding="10")
        dir_frame.pack(fill=tk.X, pady=5)
        
        ttk.Entry(dir_frame, textvariable=self.selected_dir, width=60).pack(side=tk.LEFT, padx=5)
        ttk.Button(dir_frame, text="浏览", command=self.select_directory).pack(side=tk.LEFT, padx=5)
        
        # 文件选择区域
        file_frame = ttk.LabelFrame(main_frame, text="选择CSV文件", padding="10")
        file_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 按钮区域
        btn_frame = ttk.Frame(file_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="选择所有CSV文件", command=self.select_all_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="清空选择", command=self.clear_selection).pack(side=tk.LEFT, padx=5)
        
        # 文件列表（带滚动条）
        list_frame = ttk.Frame(file_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.file_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, selectmode=tk.EXTENDED)
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.file_listbox.yview)
        
        # 操作区域（合并并处理）
        merge_frame = ttk.LabelFrame(main_frame, text="操作", padding="10")
        merge_frame.pack(fill=tk.X, pady=5)
        
        merge_inner_frame = ttk.Frame(merge_frame)
        merge_inner_frame.pack(fill=tk.X)
        
        ttk.Button(merge_inner_frame, text="合并并处理", command=self.merge_and_process).pack(side=tk.LEFT, padx=5)
        # 在按钮右侧显示合并后的条数
        self.merged_count_label = ttk.Label(merge_inner_frame, text="合并后记录数: 0", foreground="blue")
        self.merged_count_label.pack(side=tk.LEFT, padx=10)
        
        # 起始时间设置区域
        time_frame = ttk.LabelFrame(main_frame, text="起始时间设置", padding="10")
        time_frame.pack(fill=tk.X, pady=5)
        
        time_inner_frame = ttk.Frame(time_frame)
        time_inner_frame.pack(fill=tk.X)
        
        ttk.Label(time_inner_frame, text="起始时间:").pack(side=tk.LEFT, padx=5)
        self.time_entry = ttk.Entry(time_inner_frame, textvariable=self.start_time, width=20)
        self.time_entry.pack(side=tk.LEFT, padx=5)
        # 绑定事件：当输入框失去焦点时自动重新统计
        self.time_entry.bind('<FocusOut>', lambda e: self.recount_filtered_data())
        # 绑定事件：当按回车键时重新统计
        self.time_entry.bind('<Return>', lambda e: self.recount_filtered_data())
        ttk.Label(time_inner_frame, text="(格式: YYYY-MM-DD HH:MM:SS)").pack(side=tk.LEFT, padx=5)
        ttk.Button(time_inner_frame, text="重新统计", command=self.recount_filtered_data).pack(side=tk.LEFT, padx=5)
        
        # 统计结果显示
        self.count_label = ttk.Label(time_frame, text="符合条件的记录数: 0", foreground="blue")
        self.count_label.pack(pady=5)
        
        # 保存结果按钮
        save_frame = ttk.Frame(main_frame)
        save_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(save_frame, text="保存结果", command=self.save_result).pack(side=tk.LEFT, padx=5)
        ttk.Button(save_frame, text="删除文件", command=self.delete_files).pack(side=tk.LEFT, padx=5)
        ttk.Button(save_frame, text="删除图片", command=self.delete_images).pack(side=tk.LEFT, padx=5)
        
        # 状态显示区域
        status_frame = ttk.LabelFrame(main_frame, text="状态信息", padding="10")
        status_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 创建内部框架来包含文本和滚动条
        status_inner_frame = ttk.Frame(status_frame)
        status_inner_frame.pack(fill=tk.BOTH, expand=True)
        
        # 状态文本（先创建）
        self.status_text = tk.Text(status_inner_frame, height=8, wrap=tk.WORD)
        self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 状态滚动条（放在右侧，与文本相邻）
        status_scrollbar = ttk.Scrollbar(status_inner_frame, command=self.status_text.yview)
        status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_text.config(yscrollcommand=status_scrollbar.set)
    
    def log(self, message):
        """在状态区域添加日志"""
        self.status_text.insert(tk.END, f"{datetime.now().strftime('%H:%M:%S')} - {message}\n")
        self.status_text.see(tk.END)
        self.root.update()
    
    def select_directory(self):
        """选择目录"""
        # 优先使用已选择的目录，否则使用脚本所在目录
        if self.selected_dir.get() and os.path.exists(self.selected_dir.get()):
            initial_dir = self.selected_dir.get()
        else:
            initial_dir = self.default_dir
        
        # 确保路径是绝对路径且存在，并规范化路径
        if not os.path.isabs(initial_dir):
            initial_dir = os.path.abspath(initial_dir)
        initial_dir = os.path.normpath(initial_dir)  # 规范化路径（处理反斜杠等）
        if not os.path.exists(initial_dir):
            initial_dir = os.path.normpath(self.default_dir)
        
        dir_path = filedialog.askdirectory(title="选择包含CSV文件的目录", initialdir=initial_dir)
        if dir_path:
            self.selected_dir.set(dir_path)
            self.log(f"已选择目录: {dir_path}")
            # 自动列出目录下的所有CSV文件
            self.list_csv_files(dir_path)
    
    def list_csv_files(self, dir_path):
        """列出目录下的所有CSV文件（只显示以x_following开头的文件）"""
        self.file_listbox.delete(0, tk.END)
        csv_files = [f for f in os.listdir(dir_path) if f.lower().endswith('.csv') and f.startswith('x_following')]
        if csv_files:
            for f in sorted(csv_files):
                self.file_listbox.insert(tk.END, f)
            self.log(f"找到 {len(csv_files)} 个CSV文件（x_following开头）")
            # 自动全选所有文件
            self.file_listbox.selection_set(0, tk.END)
            self.log(f"已自动选择所有 {len(csv_files)} 个CSV文件")
        else:
            self.log("目录下没有找到以x_following开头的CSV文件")
    
    def select_all_csv(self):
        """选择目录下的所有CSV文件（设置列表框选中状态）"""
        if not self.selected_dir.get():
            messagebox.showwarning("警告", "请先选择目录")
            return
        
        # 设置列表框中的所有项为选中状态
        self.file_listbox.selection_set(0, tk.END)
        selected_count = len(self.file_listbox.curselection())
        self.log(f"已选择所有 {selected_count} 个CSV文件")
    
    def clear_selection(self):
        """清空选择"""
        self.file_listbox.selection_clear(0, tk.END)
        self.log("已清空文件选择")
    
    def parse_datetime(self, time_str):
        """解析时间字符串为datetime对象"""
        try:
            # 尝试解析格式：2025-11-15 13:34:27
            return datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M:%S")
        except:
            try:
                # 尝试其他常见格式
                return datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M:%S.%f")
            except:
                self.log(f"警告：无法解析时间格式: {time_str}")
                return None
    
    def read_csv_file(self, file_path):
        """读取CSV文件"""
        rows = []
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.reader(f)
                header = next(reader, None)  # 跳过表头
                
                # 检查表头格式（至少需要3列：账号名称、发推时间、推文内容）
                if header and len(header) >= 3:
                    for row in reader:
                        if len(row) >= 3:
                            # 读取前3列（必需）
                            data = {
                                'username': row[0].strip(),
                                'time': row[1].strip(),
                                'content': row[2].strip()
                            }
                            # 读取第4列（图片文件，可选，兼容旧格式）
                            if len(row) >= 4:
                                data['image_files'] = row[3].strip()
                            else:
                                data['image_files'] = ''
                            rows.append(data)
            
            self.log(f"从 {os.path.basename(file_path)} 读取了 {len(rows)} 条记录")
            return rows
        except Exception as e:
            self.log(f"读取文件 {os.path.basename(file_path)} 时出错: {e}")
            return []
    
    def merge_and_process(self):
        """合并并处理CSV文件"""
        # 从列表框的选中状态获取文件路径
        selected_indices = self.file_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("警告", "请先在列表中选择要合并的CSV文件")
            return
        
        # 获取选中的文件路径
        selected_files = []
        dir_path = self.selected_dir.get()
        if not dir_path:
            messagebox.showwarning("警告", "请先选择目录")
            return
        
        for idx in selected_indices:
            filename = self.file_listbox.get(idx)
            file_path = os.path.join(dir_path, filename)
            selected_files.append(file_path)
        
        self.log("=" * 50)
        self.log(f"开始合并和处理 {len(selected_files)} 个文件...")
        
        # 读取所有文件
        all_data = []
        for file_path in selected_files:
            rows = self.read_csv_file(file_path)
            all_data.extend(rows)
        
        self.log(f"总共读取了 {len(all_data)} 条记录")
        
        # 删除发推时间为"未知时间"或无法解析时间格式的记录
        self.log("正在过滤无效时间的记录...")
        filtered_data = []
        invalid_time_count = 0
        for row in all_data:
            time_str = row['time'].strip()
            if time_str.lower() == '未知时间' or time_str == '':
                invalid_time_count += 1
            else:
                # 尝试解析时间，如果无法解析则删除
                dt = self.parse_datetime(time_str)
                if dt is None:
                    invalid_time_count += 1
                else:
                    filtered_data.append(row)
        
        self.log(f"已删除 {invalid_time_count} 条无效时间的记录，剩余 {len(filtered_data)} 条记录")
        
        # 去重：相同账号+相同推文内容，只保留发推时间最早的一条
        self.log("正在去重...")
        unique_data = {}
        
        for row in filtered_data:
            username = row['username']
            content = row['content']
            time_str = row['time']
            
            # 解析时间，如果无法解析则跳过
            dt = self.parse_datetime(time_str)
            if dt is None:
                continue  # 跳过无法解析时间的记录
            
            # 创建唯一键：账号名称 + 推文内容
            key = (username, content)
            
            if key not in unique_data:
                # 如果不存在，直接添加
                unique_data[key] = row
                unique_data[key]['datetime'] = dt
            else:
                # 如果已存在，比较时间，保留较早的
                if 'datetime' not in unique_data[key] or dt < unique_data[key]['datetime']:
                    unique_data[key] = row
                    unique_data[key]['datetime'] = dt
        
        self.log(f"去重后剩余 {len(unique_data)} 条记录")
        
        # 转换为列表
        processed_data = list(unique_data.values())
        
        # 先按账号名称排序
        self.log("正在按账号名称排序...")
        processed_data.sort(key=lambda x: x['username'])
        
        # 再按发推时间排序
        self.log("正在按发推时间排序...")
        processed_data.sort(key=lambda x: x.get('datetime', datetime.min))
        
        # 移除临时的datetime字段
        for row in processed_data:
            if 'datetime' in row:
                del row['datetime']
        
        self.merged_data = processed_data
        
        # 更新合并后的条数显示
        self.merged_count_label.config(text=f"合并后记录数: {len(self.merged_data)}")
        
        # 统计起始时间之后的记录
        self.recount_filtered_data()
        
        self.log(f"处理完成！最终有 {len(self.merged_data)} 条记录")
        self.log("=" * 50)
        
        messagebox.showinfo("完成", f"合并和处理完成！\n最终有 {len(self.merged_data)} 条记录\n起始时间之后有 {len(self.filtered_data)} 条记录")
    
    def parse_start_time(self):
        """解析起始时间字符串"""
        try:
            time_str = self.start_time.get().strip()
            return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            self.log(f"警告：无法解析起始时间格式: {self.start_time.get()}")
            return None
    
    def recount_filtered_data(self):
        """重新统计起始时间之后的记录"""
        if not self.merged_data:
            self.filtered_data = []
            self.count_label.config(text="符合条件的记录数: 0")
            return
        
        start_dt = self.parse_start_time()
        if start_dt is None:
            self.log("警告：起始时间格式错误，无法统计")
            self.count_label.config(text="符合条件的记录数: 0 (时间格式错误)")
            return
        
        # 过滤出起始时间之后的记录
        self.filtered_data = []
        for row in self.merged_data:
            dt = self.parse_datetime(row['time'])
            if dt is not None and dt >= start_dt:
                self.filtered_data.append(row)
        
        count = len(self.filtered_data)
        self.count_label.config(text=f"符合条件的记录数: {count}")
        self.log(f"起始时间 ({self.start_time.get()}) 之后有 {count} 条记录")
    
    def save_result(self):
        """保存合并后的结果（只保存起始时间之后的记录）"""
        if not self.merged_data:
            messagebox.showwarning("警告", "没有可保存的数据，请先执行合并操作")
            return
        
        # 重新统计以确保数据是最新的
        self.recount_filtered_data()
        
        if not self.filtered_data:
            messagebox.showwarning("警告", f"起始时间 ({self.start_time.get()}) 之后没有符合条件的记录")
            return
        
        # 优先使用已选择的目录，否则使用脚本所在目录
        if self.selected_dir.get() and os.path.exists(self.selected_dir.get()):
            initial_dir = self.selected_dir.get()
        else:
            initial_dir = self.default_dir
        
        # 确保路径是绝对路径且存在，并规范化路径
        if not os.path.isabs(initial_dir):
            initial_dir = os.path.abspath(initial_dir)
        initial_dir = os.path.normpath(initial_dir)  # 规范化路径（处理反斜杠等）
        if not os.path.exists(initial_dir):
            initial_dir = os.path.normpath(self.default_dir)
        
        # 生成默认文件名：当天日期（格式：MMDD，如1218）
        today = datetime.now()
        default_filename = today.strftime("%m%d") + ".csv"
        
        filename = filedialog.asksaveasfilename(
            title="保存合并后的CSV文件",
            defaultextension=".csv",
            filetypes=[("CSV文件", "*.csv"), ("所有文件", "*.*")],
            initialdir=initial_dir,
            initialfile=default_filename
        )
        
        if not filename:
            return
        
        try:
            # 保存CSV文件
            with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                # 写入表头（包含图片文件列）
                writer.writerow(['账号名称', '发推时间', '推文内容', '图片文件'])
                # 只写入起始时间之后的数据
                for row in self.filtered_data:
                    # 获取图片文件信息（如果存在），否则使用空字符串
                    image_files = row.get('image_files', '')
                    writer.writerow([row['username'], row['time'], row['content'], image_files])
            
            # 生成TXT文件名（将.csv替换为.txt）
            txt_filename = filename.rsplit('.', 1)[0] + '.txt'
            
            # 保存TXT文件（tab分隔，推文内容中的换行和tab替换为空格）
            total_tabs = 0  # 统计tab键总数
            with open(txt_filename, 'w', encoding='utf-8') as f:
                # 写入表头（tab分隔）
                f.write('账号名称\t发推时间\t推文内容\n')
                # 只写入起始时间之后的数据
                for row in self.filtered_data:
                    # 统计原始内容中的tab键个数
                    tab_count = row['content'].count('\t')
                    total_tabs += tab_count
                    # 将推文内容中的回车换行和tab键替换为空格
                    content = row['content'].replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
                    f.write(f"{row['username']}\t{row['time']}\t{content}\n")
            
            self.log(f"CSV文件已保存到: {filename}")
            self.log(f"TXT文件已保存到: {txt_filename}")
            self.log(f"已保存 {len(self.filtered_data)} 条记录（起始时间: {self.start_time.get()}）")
            if total_tabs > 0:
                self.log(f"检测到并替换了 {total_tabs} 个Tab键")
            messagebox.showinfo("成功", f"文件已保存到:\nCSV: {filename}\nTXT: {txt_filename}\n\n已保存 {len(self.filtered_data)} 条记录\n（起始时间: {self.start_time.get()})" + (f"\n\n检测到并替换了 {total_tabs} 个Tab键" if total_tabs > 0 else ""))
        except Exception as e:
            self.log(f"保存文件时出错: {e}")
            messagebox.showerror("错误", f"保存文件时出错:\n{e}")
    
    def delete_files(self):
        """删除history_data目录下所有以x_following开头的CSV文件"""
        # 确定history_data目录路径
        history_data_dir = os.path.join(self.default_dir, 'history_data')
        if not os.path.exists(history_data_dir):
            # 如果默认目录下没有history_data，尝试使用当前选择的目录
            if self.selected_dir.get() and os.path.exists(self.selected_dir.get()):
                history_data_dir = self.selected_dir.get()
            else:
                return
        
        # 查找所有以x_following开头的CSV文件
        csv_files = [f for f in os.listdir(history_data_dir) 
                     if f.lower().endswith('.csv') and f.startswith('x_following')]
        
        if not csv_files:
            return
        
        # 执行删除
        deleted_count = 0
        failed_count = 0
        self.log("=" * 50)
        self.log(f"开始删除 {len(csv_files)} 个文件...")
        
        for filename in csv_files:
            file_path = os.path.join(history_data_dir, filename)
            try:
                os.remove(file_path)
                deleted_count += 1
                self.log(f"已删除: {filename}")
            except Exception as e:
                failed_count += 1
                self.log(f"删除失败: {filename} - {e}")
        
        self.log(f"删除完成！成功: {deleted_count} 个，失败: {failed_count} 个")
        self.log("=" * 50)
        
        # 更新文件列表
        if self.selected_dir.get() == history_data_dir:
            self.list_csv_files(history_data_dir)
    
    def delete_images(self):
        """删除x_images目录下的所有文件"""
        # 确定x_images目录路径（在程序运行的当前目录下，不是history_data目录下）
        x_images_dir = os.path.join(self.script_dir, 'x_images')
        if not os.path.exists(x_images_dir):
            return
        
        if not os.path.isdir(x_images_dir):
            return
        
        # 查找所有文件（不包括子目录）
        all_files = []
        try:
            for item in os.listdir(x_images_dir):
                item_path = os.path.join(x_images_dir, item)
                if os.path.isfile(item_path):
                    all_files.append(item)
        except Exception as e:
            return
        
        if not all_files:
            return
        
        # 执行删除
        deleted_count = 0
        failed_count = 0
        self.log("=" * 50)
        self.log(f"开始删除 {len(all_files)} 个图片文件...")
        
        for filename in all_files:
            file_path = os.path.join(x_images_dir, filename)
            try:
                os.remove(file_path)
                deleted_count += 1
                self.log(f"已删除: {filename}")
            except Exception as e:
                failed_count += 1
                self.log(f"删除失败: {filename} - {e}")
        
        self.log(f"删除完成！成功: {deleted_count} 个，失败: {failed_count} 个")
        self.log("=" * 50)


def main():
    root = tk.Tk()
    app = CSVMergerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

