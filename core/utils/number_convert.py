def decimal_to_binary(number, precision=8):
    """
    将小数转换为二进制字符串
    Args:
        number: 需要转换的小数
        precision: 小数部分的精度(二进制位数)
    Returns:
        二进制字符串表示
    """
    # 分离整数部分和小数部分
    integer_part = int(number)
    decimal_part = number - integer_part
    
    # 转换整数部分
    integer_binary = bin(integer_part)[2:]  # 去掉'0b'前缀
    
    # 转换小数部分
    decimal_binary = []
    for _ in range(precision):
        decimal_part *= 2
        bit = int(decimal_part)
        decimal_binary.append(str(bit))
        decimal_part -= bit
    
    # 组合结果
    if decimal_binary:
        return f"{integer_binary}.{''.join(decimal_binary)}"
    return integer_binary 

def float_to_binary(number):
    """
    将浮点数转换为IEEE 754单精度(32位)二进制表示
    Args:
        number: 需要转换的浮点数
    Returns:
        32位二进制字符串，包含符号位(1位)、指数位(8位)和尾数位(23位)
    """
    import struct
    
    # 使用struct将float打包成4字节(32位)
    # 获取整数表示
    binary = struct.unpack('!I', struct.pack('!f', number))[0]
    
    # 转换成32位二进制字符串
    binary_str = format(binary, '032b')
    
    # 分离各个部分
    sign = binary_str[0]  # 符号位(1位)
    exponent = binary_str[1:9]  # 指数位(8位)
    mantissa = binary_str[9:]  # 尾数位(23位)
    
    # 格式化输出
    return {
        'binary': binary_str,
        'sign': sign,  # 0表示正数，1表示负数
        'exponent': exponent,  # 指数部分
        'mantissa': mantissa,  # 尾数部分
        'formatted': f"{sign} {exponent} {mantissa}"  # 格式化显示
    } 

def double_to_binary(number):
    """
    将浮点数转换为IEEE 754双精度(64位)二进制表示
    Args:
        number: 需要转换的浮点数
    Returns:
        64位二进制字符串，包含符号位(1位)、指数位(11位)和尾数位(52位)
    """
    import struct
    
    # 使用struct将double打包成8字节(64位)
    # 获取整数表示
    binary = struct.unpack('!Q', struct.pack('!d', number))[0]
    
    # 转换成64位二进制字符串
    binary_str = format(binary, '064b')
    
    # 分离各个部分
    sign = binary_str[0]  # 符号位(1位)
    exponent = binary_str[1:12]  # 指数位(11位)
    mantissa = binary_str[12:]  # 尾数位(52位)
    
    # 格式化输出
    return {
        'binary': binary_str,
        'sign': sign,  # 0表示正数，1表示负数
        'exponent': exponent,  # 指数部分(偏移值1023)
        'mantissa': mantissa,  # 尾数部分
        'formatted': f"{sign} {exponent} {mantissa}"  # 格式化显示
    }

def test_double_precision(number):
    """
    测试双精度浮点数的存储和读取过程
    Args:
        number: 输入的浮点数
    """
    import struct
    
    # 将浮点数转换为二进制存储格式
    binary = struct.pack('!d', number)
    
    # 从二进制格式读回浮点数
    restored = struct.unpack('!d', binary)[0]
    
    # 获取二进制表示
    binary_repr = double_to_binary(number)
    
    print(f"原始数值: {number}")
    print(f"存储后读取的数值: {restored:.20f}")  # 显示20位小数
    #print(f"二进制表示:")
    #print(f"符号位: {binary_repr['sign']}")
    #print(f"指数位: {binary_repr['exponent']}")
    #print(f"尾数位: {binary_repr['mantissa']}")
    #print(f"格式化的二进制: {binary_repr['formatted']}")
    print("-" * 50)

# 测试 0.01
for i in range(100):
    test_double_precision(i+0.01)

def explain_double(number):
    """
    解释双精度浮点数的存储格式
    """
    import struct
    
    # 获取二进制表示
    binary = struct.unpack('!Q', struct.pack('!d', number))[0]
    binary_str = format(binary, '064b')
    
    # 分解各部分
    sign = binary_str[0]
    exponent_bits = binary_str[1:12]
    mantissa_bits = binary_str[12:]
    
    # 计算实际指数值
    exponent = int(exponent_bits, 2) - 1023  # 减去偏移值
    
    # 计算实际尾数值
    mantissa = 1.0  # 隐含的前导1
    for i, bit in enumerate(mantissa_bits):
        if bit == '1':
            mantissa += 2 ** -(i + 1)
    
    print(f"数字: {number}")
    print(f"符号位: {sign} ({'负' if sign == '1' else '正'})")
    print(f"指数位: {exponent_bits} (实际指数值: {exponent})")
    print(f"尾数位: {mantissa_bits}")
    print(f"实际值 = {'-' if sign == '1' else ''}{mantissa} × 2^{exponent}")

# 测试
explain_double(227.48)


