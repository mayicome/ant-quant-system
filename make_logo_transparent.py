#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 ant.png 的背景转换为透明
"""

from PIL import Image
import os

def make_background_transparent(image_path, output_path=None, tolerance=30, bg_color=None):
    """
    将图片背景转换为透明
    
    Args:
        image_path: 输入图片路径
        output_path: 输出图片路径，如果为None则覆盖原文件
        tolerance: 颜色容差，用于检测背景色（0-255）
        bg_color: 指定的背景色 (R, G, B)，如果为None则自动检测
    """
    try:
        # 打开图片
        img = Image.open(image_path)
        print(f"原始图片模式: {img.mode}, 尺寸: {img.size}")
        
        # 转换为RGBA模式（支持透明通道）
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # 检测背景色
        if bg_color is None:
            # 检测背景色（通常是最常见的颜色，或者四个角的颜色）
            width, height = img.size
            corner_colors = [
                img.getpixel((0, 0)),  # 左上角
                img.getpixel((width-1, 0)),  # 右上角
                img.getpixel((0, height-1)),  # 左下角
                img.getpixel((width-1, height-1))  # 右下角
            ]
            
            # 如果图片已经是RGBA，取RGB部分
            if len(corner_colors[0]) == 4:
                bg_color = corner_colors[0][:3]  # 取RGB，忽略Alpha
            else:
                bg_color = corner_colors[0]
            
            print(f"自动检测到的背景色（参考左上角）: {bg_color}")
        else:
            print(f"使用指定的背景色: {bg_color}")
        
        # 获取图片数据
        data = img.getdata()
        
        # 创建新的数据列表
        new_data = []
        transparent_count = 0
        for item in data:
            # 如果是RGBA模式
            if len(item) == 4:
                r, g, b, a = item
            else:
                r, g, b = item[:3]
                a = 255
            
            # 计算与背景色的距离
            color_diff = (
                abs(r - bg_color[0]) +
                abs(g - bg_color[1]) +
                abs(b - bg_color[2])
            )
            
            # 如果颜色接近背景色，设置为透明
            if color_diff <= tolerance:
                new_data.append((r, g, b, 0))  # 完全透明
                transparent_count += 1
            else:
                new_data.append((r, g, b, a))  # 保持原样
        
        # 更新图片数据
        img.putdata(new_data)
        
        print(f"已将 {transparent_count} 个像素设置为透明")
        
        # 保存图片
        if output_path is None:
            output_path = image_path
        
        img.save(output_path, 'PNG')
        print(f"已成功将背景转换为透明: {output_path}")
        return True
        
    except Exception as e:
        print(f"处理失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    image_path = os.path.join(current_dir, 'ant.png')
    
    if not os.path.exists(image_path):
        print(f"❌ 文件不存在: {image_path}")
    else:
        print(f"正在处理: {image_path}")
        # 先备份原文件
        backup_path = image_path + '.backup'
        if not os.path.exists(backup_path):
            from shutil import copy
            copy(image_path, backup_path)
            print(f"已备份原文件到: {backup_path}")
        
        # 处理图片，容差设置为30（可以根据实际情况调整）
        make_background_transparent(image_path, tolerance=30)

