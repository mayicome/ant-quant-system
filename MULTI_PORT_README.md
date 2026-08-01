# 多端口Web应用使用说明

## 🚀 快速启动

### 方法一：使用多端口启动器（推荐）
```bash
python multi_port_web.py 8080 10000 8081 8082
```

### 方法二：使用默认端口
```bash
python multi_port_web.py
```

## 📝 版权信息管理

### 查看版权信息
```bash
python manage_copyright.py
```

### 手动编辑版权信息
版权信息文件位于 `copyrights/` 目录下：
- `8080.html` - 端口8080的版权信息
- `8081.html` - 端口8081的版权信息
- `8082.html` - 端口8082的版权信息
- `8083.html` - 端口8083的版权信息

### 版权信息格式
支持纯文本和HTML格式：

**纯文本格式：**
```
2025 关键价格计算器 - 蚂蚁量化乐园（公众号）
```

**HTML格式：**
```html
<div style="text-align: center; margin-top: 20px;">
    <img src="your-image-url" alt="Logo" style="width: 60px; height: 60px;">
    <br>
    <span style="color: #666; font-size: 12px;">蚂蚁量化乐园（公众号）提供技术支持</span>
</div>
```

## 🌐 访问地址

启动后可以通过以下地址访问：
- `http://localhost:8080` - 显示纯文本版权信息
- `http://localhost:8081` - 显示端口8081的版权信息
- `http://localhost:8082` - 显示端口8082的版权信息
- `http://localhost:8083` - 显示端口8083的版权信息

## 🛠️ 自定义端口

### 创建新端口的版权信息
1. 运行版权管理工具：
   ```bash
   python manage_copyright.py
   ```
2. 选择"创建/编辑版权信息"
3. 输入端口号和版权内容

### 直接创建版权文件
在 `copyrights/` 目录下创建 `端口号.html` 文件，例如：
- `9000.html` - 端口9000的版权信息
- `3000.html` - 端口3000的版权信息

## 📋 功能特点

- ✅ **多端口支持**：同时启动多个端口的Web服务
- ✅ **独立版权信息**：每个端口显示不同的版权信息
- ✅ **HTML支持**：版权信息支持HTML格式，可添加图片、链接等
- ✅ **管理工具**：提供图形化管理界面
- ✅ **默认配置**：自动创建常用端口的默认版权信息

## 🔧 技术实现

- **Flask应用**：每个端口运行独立的Flask应用实例
- **版权管理器**：`CopyrightManager` 类管理版权信息
- **文件系统**：版权信息存储在 `copyrights/` 目录
- **模板渲染**：使用Jinja2模板渲染版权信息

## 📁 文件结构

```
├── multi_port_web.py      # 多端口启动器
├── copyright_manager.py   # 版权信息管理器
├── manage_copyright.py    # 版权信息管理工具
├── copyrights/           # 版权信息目录
│   ├── 8080.html        # 端口8080版权信息
│   ├── 8081.html        # 端口8081版权信息
│   ├── 8082.html        # 端口8082版权信息
│   ├── 8083.html        # 端口8083版权信息
│   └── ...
└── README.md            # 使用说明
```

## 🎯 使用示例

### 启动多个端口
```bash
# 启动8080和8081端口
python multi_port_web.py 8080 8081

# 启动默认端口组
python multi_port_web.py
```

### 管理版权信息
```bash
# 打开版权管理工具
python manage_copyright.py

# 选择操作：
# 1. 查看所有版权信息
# 2. 创建/编辑版权信息
# 3. 删除版权信息
# 4. 初始化默认版权信息
```

### 自定义版权信息
编辑 `copyrights/8080.html`：
```html
<div style="text-align: center; color: #666;">
    <strong>2025 关键价格计算器</strong><br>
    蚂蚁量化乐园（公众号）<br>
    <small>专业量化交易工具</small>
</div>
```
