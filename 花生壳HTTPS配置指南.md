# 花生壳HTTPS配置完整指南

## 📋 花生壳版本对比

### 免费版（体验版）
- ✅ 支持HTTP和HTTPS转发
- ✅ 可以使用自签名证书
- ❌ 不支持自定义域名（只能使用花生壳提供的免费域名）
- ⚠️ 有带宽和流量限制
- ⚠️ 域名可能不稳定

### 付费版（专业版/旗舰版）
- ✅ 支持HTTP和HTTPS转发
- ✅ 支持自定义域名
- ✅ 可以使用Let's Encrypt证书
- ✅ 带宽和流量更大
- ✅ 域名更稳定

---

## 🎯 方案选择

### 方案1：自签名证书（免费版可用）⭐推荐免费用户

**优点：**
- ✅ 完全免费
- ✅ 免费版花生壳即可使用
- ✅ 配置简单
- ✅ 支持HTTPS加密传输

**缺点：**
- ⚠️ 浏览器会显示"不安全"警告
- ⚠️ 需要手动点击"继续访问"
- ⚠️ 每次访问都需要确认

**适用场景：**
- 个人使用
- 能接受浏览器警告
- 不想付费

---

### 方案2：Let's Encrypt证书（需要付费版）

**优点：**
- ✅ 证书完全免费
- ✅ 浏览器完全信任，无警告
- ✅ 自动续期

**缺点：**
- 💰 需要花生壳付费版（支持自定义域名）
- 💰 需要购买域名（约10-50元/年）
- ⚠️ 配置相对复杂

**适用场景：**
- 需要专业展示
- 不想看到浏览器警告
- 愿意付费使用花生壳

---

## 🚀 方案1：自签名证书配置（推荐免费用户）

### 步骤1：生成自签名证书

#### Windows方法1：使用OpenSSL（推荐）

1. **下载OpenSSL**
   - 访问：https://slproweb.com/products/Win32OpenSSL.html
   - 下载并安装 Win64 OpenSSL（Light版本即可）

2. **生成证书**
   ```powershell
   # 打开PowerShell，进入项目目录
   cd "D:\蚂蚁量化交易策略第四版"
   
   # 生成私钥和证书（有效期1年）
   openssl req -x509 -newkey rsa:2048 -nodes -keyout ssl_key.pem -out ssl_cert.pem -days 365 -subj "/CN=localhost"
   ```

3. **验证证书文件**
   - 应该生成两个文件：
     - `ssl_key.pem`（私钥）
     - `ssl_cert.pem`（证书）

#### Windows方法2：使用Python（无需安装OpenSSL）

创建一个Python脚本自动生成证书：

```python
# generate_self_signed_cert.py
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from datetime import datetime, timedelta

# 生成私钥
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
)

# 创建证书
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
    x509.NameAttribute(NameOID.LOCALITY_NAME, "Local"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "My Company"),
    x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
])

cert = x509.CertificateBuilder().subject_name(
    subject
).issuer_name(
    issuer
).public_key(
    private_key.public_key()
).serial_number(
    x509.random_serial_number()
).not_valid_before(
    datetime.utcnow()
).not_valid_after(
    datetime.utcnow() + timedelta(days=365)
).add_extension(
    x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.DNSName("127.0.0.1"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]),
    critical=False,
).sign(private_key, hashes.SHA256())

# 保存私钥
with open("ssl_key.pem", "wb") as f:
    f.write(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ))

# 保存证书
with open("ssl_cert.pem", "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("✅ 证书生成成功！")
print("   - ssl_key.pem (私钥)")
print("   - ssl_cert.pem (证书)")
```

---

### 步骤2：配置Web应用使用HTTPS

#### 对于 web_app.py：

```python
# 在文件末尾的启动部分修改
if __name__ == '__main__':
    import os
    port = int(os.environ.get('FLASK_PORT', 8080))
    
    # 检查证书文件是否存在
    cert_file = 'ssl_cert.pem'
    key_file = 'ssl_key.pem'
    
    if os.path.exists(cert_file) and os.path.exists(key_file):
        print("✅ 检测到SSL证书，启用HTTPS模式")
        app.run(host='0.0.0.0', port=port, debug=True, 
                ssl_context=(cert_file, key_file))
    else:
        print("⚠️  未找到SSL证书，使用HTTP模式")
        app.run(host='0.0.0.0', port=port, debug=True)
```

#### 对于 limit_up_monitor_web.py：

```python
# 在文件末尾的启动部分修改
if __name__ == '__main__':
    # ... 端口检测代码 ...
    
    # 检查证书文件
    cert_file = 'ssl_cert.pem'
    key_file = 'ssl_key.pem'
    
    if os.path.exists(cert_file) and os.path.exists(key_file):
        print("✅ 检测到SSL证书，启用HTTPS模式")
        try:
            app.run(host='127.0.0.1', port=port, debug=False,
                    ssl_context=(cert_file, key_file))
        except Exception as e:
            print(f"启动失败: {e}")
    else:
        print("⚠️  未找到SSL证书，使用HTTP模式")
        try:
            app.run(host='127.0.0.1', port=port, debug=False)
        except Exception as e:
            print(f"启动失败: {e}")
```

---

### 步骤3：配置花生壳映射

1. **登录花生壳管理界面**
   - 访问：https://hsk.oray.com/
   - 登录您的账号

2. **添加映射**
   - 点击"内网穿透" → "添加映射"
   - 配置如下：
     ```
     应用名称：量化交易Web应用
     映射类型：HTTPS（或HTTP，花生壳会自动处理）
     内网主机：127.0.0.1（或您的内网IP）
     内网端口：8080（或您Web应用使用的端口）
     外网域名：使用花生壳提供的免费域名（免费版）
             或绑定您的自定义域名（付费版）
     外网端口：443（HTTPS）或80（HTTP）
     ```

3. **保存并启用映射**

---

### 步骤4：访问测试

1. **启动Web应用**
   ```powershell
   python web_app.py
   # 或
   python limit_up_monitor_web.py
   ```

2. **通过花生壳域名访问**
   - 使用花生壳提供的外网地址访问
   - 例如：`https://yourname.gicp.net:端口`

3. **处理浏览器警告**
   - 浏览器会显示"您的连接不是私密连接"
   - 点击"高级"
   - 点击"继续前往 yourname.gicp.net（不安全）"
   - 之后可以正常使用HTTPS加密

---

## 🔐 方案2：Let's Encrypt证书配置（付费版用户）

### 前置条件

1. ✅ 花生壳付费版（支持自定义域名）
2. ✅ 拥有自己的域名（如：yourdomain.com）
3. ✅ 域名DNS已解析到花生壳服务器

---

### 步骤1：在花生壳绑定自定义域名

1. **登录花生壳管理界面**
2. **添加映射时选择自定义域名**
   - 外网域名：yourdomain.com
   - 确保域名DNS已解析到花生壳提供的IP

---

### 步骤2：获取Let's Encrypt证书

#### 方法1：使用certbot（推荐）

1. **安装certbot**
   ```powershell
   # Windows可以使用WSL或直接下载Windows版本
   # 下载地址：https://certbot.eff.org/
   ```

2. **获取证书**
   ```bash
   # 使用standalone模式（需要临时停止Web应用）
   certbot certonly --standalone -d yourdomain.com
   
   # 或使用webroot模式（不需要停止Web应用）
   certbot certonly --webroot -w ./static -d yourdomain.com
   ```

3. **证书位置**
   - 证书：`C:\Certbot\live\yourdomain.com\fullchain.pem`
   - 私钥：`C:\Certbot\live\yourdomain.com\privkey.pem`

#### 方法2：使用acme.sh（跨平台）

```bash
# 安装acme.sh
curl https://get.acme.sh | sh

# 获取证书
acme.sh --issue -d yourdomain.com --standalone
```

---

### 步骤3：配置Web应用

```python
# 使用Let's Encrypt证书
cert_file = r'C:\Certbot\live\yourdomain.com\fullchain.pem'
key_file = r'C:\Certbot\live\yourdomain.com\privkey.pem'

app.run(host='0.0.0.0', port=8080, debug=True,
        ssl_context=(cert_file, key_file))
```

---

### 步骤4：配置自动续期

Let's Encrypt证书有效期90天，需要自动续期：

```powershell
# 创建续期脚本 renew_cert.bat
certbot renew --quiet

# 添加到Windows计划任务，每月执行一次
```

---

## 📝 花生壳具体配置示例

### 场景1：使用免费版 + 自签名证书

```
花生壳映射配置：
├─ 应用名称：量化交易HTTPS
├─ 映射类型：HTTPS
├─ 内网主机：127.0.0.1
├─ 内网端口：8080
├─ 外网域名：yourname.gicp.net（花生壳免费域名）
└─ 外网端口：443

Web应用配置：
├─ 使用自签名证书（ssl_cert.pem, ssl_key.pem）
├─ 监听端口：8080
└─ 绑定地址：0.0.0.0

访问方式：
└─ https://yourname.gicp.net:443
   （浏览器会显示警告，需要手动继续）
```

---

### 场景2：使用付费版 + Let's Encrypt证书

```
花生壳映射配置：
├─ 应用名称：量化交易HTTPS专业版
├─ 映射类型：HTTPS
├─ 内网主机：127.0.0.1
├─ 内网端口：8080
├─ 外网域名：yourdomain.com（自定义域名）
└─ 外网端口：443

域名DNS配置：
└─ yourdomain.com A记录 → 花生壳服务器IP

Web应用配置：
├─ 使用Let's Encrypt证书
├─ 监听端口：8080
└─ 绑定地址：0.0.0.0

访问方式：
└─ https://yourdomain.com
   （浏览器完全信任，无警告）
```

---

## ⚠️ 注意事项

### 花生壳免费版限制

1. **带宽限制**
   - 免费版通常有1-2Mbps带宽限制
   - 可能影响HTTPS传输速度

2. **流量限制**
   - 每月有流量限制（通常几GB）
   - HTTPS会增加一些流量开销

3. **域名限制**
   - 只能使用花生壳提供的免费域名
   - 域名可能不稳定或变更

4. **端口限制**
   - 某些端口可能被限制
   - 建议使用443（HTTPS）或80（HTTP）

---

### 自签名证书注意事项

1. **浏览器警告**
   - Chrome/Edge：显示"您的连接不是私密连接"
   - Firefox：显示"警告：潜在的安全风险"
   - 需要每次手动确认（可以添加例外）

2. **移动端访问**
   - 移动浏览器也会显示警告
   - 需要手动确认继续访问

3. **证书有效期**
   - 建议设置1年有效期
   - 到期前需要重新生成

---

### Let's Encrypt证书注意事项

1. **域名验证**
   - 必须能够通过80端口访问您的域名
   - 花生壳需要支持HTTP-01验证

2. **证书续期**
   - 90天有效期
   - 必须配置自动续期
   - 续期后需要重启Web应用

3. **花生壳配置**
   - 确保花生壳映射支持80端口（用于验证）
   - 确保443端口可用（用于HTTPS服务）

---

## 🔧 故障排查

### 问题1：浏览器无法访问HTTPS

**可能原因：**
- 证书文件路径错误
- 证书文件权限问题
- 花生壳映射未正确配置

**解决方法：**
1. 检查证书文件是否存在
2. 检查Web应用日志中的错误信息
3. 确认花生壳映射状态为"在线"

---

### 问题2：Let's Encrypt验证失败

**可能原因：**
- 域名DNS未正确解析
- 80端口无法访问
- 花生壳未正确转发80端口

**解决方法：**
1. 检查域名DNS解析
2. 测试80端口是否可访问：`http://yourdomain.com`
3. 确认花生壳映射包含80端口

---

### 问题3：证书过期

**解决方法：**
1. 自签名证书：重新生成证书
2. Let's Encrypt：运行续期命令
3. 重启Web应用加载新证书

---

## 💡 推荐配置

### 免费用户推荐

```
方案：自签名证书 + 花生壳免费版
成本：完全免费
配置难度：简单
浏览器体验：需要手动确认警告
```

### 付费用户推荐

```
方案：Let's Encrypt证书 + 花生壳付费版 + 自定义域名
成本：花生壳付费（约100-300元/年）+ 域名（10-50元/年）
配置难度：中等
浏览器体验：完全正常，无警告
```

---

## 📞 需要帮助？

如果您在配置过程中遇到问题，请告诉我：
1. 您使用的是花生壳免费版还是付费版？
2. 您是否有自己的域名？
3. 您遇到了什么具体错误？

我可以为您提供更详细的帮助！

