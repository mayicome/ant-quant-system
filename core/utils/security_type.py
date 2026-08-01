class SecurityTypeUtil:
    @staticmethod
    def is_fund(security_code: str) -> bool:
        """判断是否为基金/ETF（价格精度一般为 0.001）。"""
        # 移除.SH/.SZ/.BJ后缀（如果有）
        code = security_code.split('.')[0] if '.' in security_code else security_code
        if len(code) != 6 or not code.isdigit():
            return False
        # 沪市：5xxxxx；深市 ETF：15/16/18 开头
        return code.startswith("5") or code.startswith(("15", "16", "18"))
    
    @staticmethod
    def get_price_precision(security_code: str) -> int:
        """获取价格精度"""
        return 3 if SecurityTypeUtil.is_fund(security_code) else 2
    
    @staticmethod
    def round_price(security_code: str, price: float) -> float:
        """根据证券类型对价格进行舍入"""
        precision = SecurityTypeUtil.get_price_precision(security_code)
        return round(price, precision)

    @staticmethod
    def min_price_tick(security_code: str) -> float:
        return 10 ** (-SecurityTypeUtil.get_price_precision(security_code))