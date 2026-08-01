"""
为历史数据文件补充概念统计和板块统计
遍历history_data目录下的所有JSON文件，为每只股票补充概念和板块信息，
并计算concept_stats和sector_plate_stats
"""
import os
import json
from datetime import date

# 历史数据目录
HISTORY_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'history_data')

def calculate_concept_stats(stocks):
    """计算概念题材统计"""
    concept_count = {}
    
    for stock in stocks:
        # 统计概念题材
        concepts = stock.get('concepts', [])
        if concepts and isinstance(concepts, list):
            for concept in concepts:
                if concept and str(concept).strip():
                    concept_name = str(concept).strip()
                    if concept_name not in concept_count:
                        concept_count[concept_name] = {
                            'name': concept_name,
                            'count': 0,
                            'stocks': []
                        }
                    concept_count[concept_name]['count'] += 1
                    # 确保股票代码格式化为6位
                    code = str(stock.get('code', '')).zfill(6)
                    stock_str = f"{code} {stock['name']}"
                    if stock_str not in concept_count[concept_name]['stocks']:
                        concept_count[concept_name]['stocks'].append(stock_str)
    
    # 转换为列表并按涨停数量排序，只保留数量>=2的概念
    concept_stats = [item for item in concept_count.values() if item['count'] >= 2]
    concept_stats.sort(key=lambda x: x['count'], reverse=True)
    
    return concept_stats


def calculate_sector_plate_stats(stocks):
    """计算板块统计"""
    sector_plate_count = {}
    
    for stock in stocks:
        # 统计板块
        plates = stock.get('plates', [])
        if plates and isinstance(plates, list):
            for plate in plates:
                if plate and str(plate).strip():
                    plate_name = str(plate).strip()
                    if plate_name not in sector_plate_count:
                        sector_plate_count[plate_name] = {
                            'name': plate_name,
                            'count': 0,
                            'stocks': []
                        }
                    sector_plate_count[plate_name]['count'] += 1
                    # 确保股票代码格式化为6位
                    code = str(stock.get('code', '')).zfill(6)
                    stock_str = f"{code} {stock['name']}"
                    if stock_str not in sector_plate_count[plate_name]['stocks']:
                        sector_plate_count[plate_name]['stocks'].append(stock_str)
    
    # 转换为列表并按涨停数量排序，只保留数量>=2的板块
    sector_plate_stats = [item for item in sector_plate_count.values() if item['count'] >= 2]
    sector_plate_stats.sort(key=lambda x: x['count'], reverse=True)
    
    return sector_plate_stats


def supplement_file(file_path):
    """补充单个文件的概念和板块统计"""
    try:
        # 读取文件
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 检查是否已经有concept_stats和sector_plate_stats
        has_concept_stats = 'concept_stats' in data and data.get('concept_stats')
        has_sector_plate_stats = 'sector_plate_stats' in data and data.get('sector_plate_stats')
        
        if has_concept_stats and has_sector_plate_stats:
            print(f"文件 {os.path.basename(file_path)} 已有完整统计，跳过")
            return True
        
        stocks = data.get('limit_up_stocks', [])
        if not stocks:
            print(f"文件 {os.path.basename(file_path)} 没有股票数据，跳过")
            return True
        
        # 加载股票信息JSON文件
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, 'data', 'all_a_stock_info.json')
        stock_info_dict = {}
        
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    stock_info_dict = json.load(f)
                print(f"成功加载股票信息文件，包含 {len(stock_info_dict)} 只股票的信息")
            except Exception as e:
                print(f"加载股票信息文件失败: {e}")
                return False
        else:
            print(f"股票信息文件不存在: {json_path}")
            return False
        
        # 为每只股票补充概念和板块信息
        stocks_updated = False
        for stock in stocks:
            code = str(stock.get('code', '')).zfill(6)
            stock_info = stock_info_dict.get(code, {})
            
            # 补充概念信息
            if 'concepts' not in stock or not stock.get('concepts'):
                concepts = stock_info.get('concepts', [])
                if concepts and isinstance(concepts, list):
                    stock['concepts'] = concepts
                    stocks_updated = True
                else:
                    stock['concepts'] = []
            
            # 补充板块信息
            if 'plates' not in stock or not stock.get('plates'):
                plates = stock_info.get('plates', [])
                if plates and isinstance(plates, list):
                    stock['plates'] = plates
                    stocks_updated = True
                else:
                    stock['plates'] = []
        
        # 计算概念统计
        if not has_concept_stats:
            concept_stats = calculate_concept_stats(stocks)
            data['concept_stats'] = concept_stats
            print(f"  计算概念统计: {len(concept_stats)} 个概念")
        
        # 计算板块统计
        if not has_sector_plate_stats:
            sector_plate_stats = calculate_sector_plate_stats(stocks)
            data['sector_plate_stats'] = sector_plate_stats
            print(f"  计算板块统计: {len(sector_plate_stats)} 个板块")
        
        # 保存文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"✓ 成功补充文件: {os.path.basename(file_path)}")
        return True
        
    except Exception as e:
        print(f"✗ 处理文件 {os.path.basename(file_path)} 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    from utils.limit_up_day_path import list_limit_up_day_json_files

    if not os.path.exists(HISTORY_DATA_DIR):
        print(f"历史数据目录不存在: {HISTORY_DATA_DIR}")
        return
    
    # 获取所有涨停日 JSON（新子目录 + 旧根目录兼容）
    json_files = [fp for _, fp in list_limit_up_day_json_files(HISTORY_DATA_DIR)]
    
    if not json_files:
        print("没有找到历史数据JSON文件")
        return
    
    print(f"找到 {len(json_files)} 个历史数据文件")
    print("-" * 60)
    
    success_count = 0
    fail_count = 0
    
    for file_path in sorted(json_files):
        if supplement_file(file_path):
            success_count += 1
        else:
            fail_count += 1
        print()
    
    print("-" * 60)
    print(f"处理完成: 成功 {success_count} 个，失败 {fail_count} 个")


if __name__ == '__main__':
    main()

