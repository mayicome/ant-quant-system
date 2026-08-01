# -*- coding: utf-8 -*-
"""直接处理 ant.png，将背景转换为透明"""
import os
from PIL import Image

current_dir = os.path.dirname(os.path.abspath(__file__))
image_path = os.path.join(current_dir, 'ant.png')

if not os.path.exists(image_path):
    print(f"文件不存在: {image_path}")
else:
    try:
        # 打开图片
        img = Image.open(image_path)
        print(f"原始图片模式: {img.mode}, 尺寸: {img.size}")
        
        # 转换为RGBA模式
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # 检测背景色（取四个角的颜色）
        width, height = img.size
        corner = img.getpixel((0, 0))
        if len(corner) == 4:
            bg_color = corner[:3]
        else:
            bg_color = corner
        
        print(f"检测到的背景色: {bg_color}")
        
        # 处理每个像素
        data = img.getdata()
        new_data = []
        tolerance = 30
        transparent_count = 0
        
        for item in data:
            if len(item) == 4:
                r, g, b, a = item
            else:
                r, g, b = item[:3]
                a = 255
            
            # 计算颜色差异
            diff = abs(r - bg_color[0]) + abs(g - bg_color[1]) + abs(b - bg_color[2])
            
            if diff <= tolerance:
                new_data.append((r, g, b, 0))
                transparent_count += 1
            else:
                new_data.append((r, g, b, a))
        
        img.putdata(new_data)
        
        # 备份原文件
        backup_path = image_path + '.backup'
        if not os.path.exists(backup_path):
            from shutil import copy
            copy(image_path, backup_path)
            print(f"已备份原文件到: {backup_path}")
        
        # 保存处理后的图片
        img.save(image_path, 'PNG')
        print(f"已将 {transparent_count} 个像素设置为透明")
        print(f"处理完成！文件已保存: {image_path}")
        
    except Exception as e:
        print(f"处理失败: {e}")
        import traceback
        traceback.print_exc()

