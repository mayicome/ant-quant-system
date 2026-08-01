"""
清除 all_a_stock_info.json 文件中所有以"昨日"开头的板块名称
"""
import json
import os

def clean_yesterday_plates():
    """清除所有以"昨日"开头的板块名称"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, 'data', 'all_a_stock_info.json')
    
    if not os.path.exists(file_path):
        print(f"文件不存在: {file_path}")
        return
    
    print(f"正在读取文件: {file_path}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            stock_info_dict = json.load(f)
        print(f"成功加载文件，包含 {len(stock_info_dict)} 只股票的信息")
    except Exception as e:
        print(f"读取文件失败: {e}")
        return
    
    # 统计信息
    total_removed_plates = 0
    total_removed_concepts = 0
    stocks_affected = 0
    
    # 遍历所有股票，清除以"昨日"开头的板块和概念
    print("正在清除以\"昨日\"开头的板块和概念...")
    for stock_code, stock_info in stock_info_dict.items():
        removed_plates = 0
        removed_concepts = 0
        
        # 清除 plates 数组中以"昨日"开头的板块
        if 'plates' in stock_info and isinstance(stock_info['plates'], list):
            original_count = len(stock_info['plates'])
            stock_info['plates'] = [
                plate for plate in stock_info['plates'] 
                if not plate.startswith('昨日')
            ]
            removed_plates = original_count - len(stock_info['plates'])
            total_removed_plates += removed_plates
        
        # 清除 concepts 数组中以"昨日"开头的概念
        if 'concepts' in stock_info and isinstance(stock_info['concepts'], list):
            original_count = len(stock_info['concepts'])
            stock_info['concepts'] = [
                concept for concept in stock_info['concepts'] 
                if not concept.startswith('昨日')
            ]
            removed_concepts = original_count - len(stock_info['concepts'])
            total_removed_concepts += removed_concepts
        
        if removed_plates > 0 or removed_concepts > 0:
            stocks_affected += 1
            msg_parts = []
            if removed_plates > 0:
                msg_parts.append(f"移除了 {removed_plates} 个板块")
            if removed_concepts > 0:
                msg_parts.append(f"移除了 {removed_concepts} 个概念")
            print(f"  股票 {stock_code}: {', '.join(msg_parts)}")
    
    print(f"\n处理完成:")
    print(f"  - 受影响股票数: {stocks_affected}")
    print(f"  - 移除板块总数: {total_removed_plates}")
    print(f"  - 移除概念总数: {total_removed_concepts}")
    
    # 保存修改后的文件
    print(f"\n正在保存文件...")
    try:
        # 先备份原文件
        backup_path = file_path + '.backup'
        if not os.path.exists(backup_path):
            print(f"正在创建备份文件: {backup_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                backup_content = f.read()
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(backup_content)
            print(f"备份文件创建成功")
        
        # 保存修改后的文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(stock_info_dict, f, ensure_ascii=False, indent=2)
        print(f"文件保存成功: {file_path}")
    except Exception as e:
        print(f"保存文件失败: {e}")

if __name__ == '__main__':
    clean_yesterday_plates()

