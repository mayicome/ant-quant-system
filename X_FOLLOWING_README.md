# X Following 推文提取工具使用说明

## 功能说明

这个工具可以从已打开的浏览器中获取X(Twitter) Following页面的最新推文内容，并保存到文件中。

## 使用前准备

### 1. 安装依赖

确保已安装Selenium：
```bash
pip install selenium
```

### 2. 启动Chrome浏览器（调试模式）

**重要**：必须先以调试模式启动Chrome浏览器，程序才能连接到已打开的浏览器。

#### Windows系统：

1. 关闭所有Chrome浏览器窗口
2. 打开命令提示符（CMD）或PowerShell
3. 运行以下命令（根据你的Chrome安装路径调整）：

```bash
# 方法1：使用默认Chrome路径
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome_debug"

# 方法2：如果Chrome在其他位置，找到chrome.exe的完整路径
# 例如：D:\Chrome\chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome_debug"
```

#### 参数说明：
- `--remote-debugging-port=9222`：启用远程调试端口（默认9222）
- `--user-data-dir="C:\temp\chrome_debug"`：使用临时用户数据目录（避免影响正常浏览器）

### 3. 登录X账号并打开Following页面

1. 在调试模式启动的Chrome浏览器中，访问 https://x.com 或 https://twitter.com
2. 登录你的X账号
3. 导航到Following页面（点击左侧菜单的"Following"或访问 https://x.com/following）

## 运行程序

```bash
python get_x_following.py
```

## 输出文件

程序会生成两个文件：

1. **CSV文件**（如：`x_following_20241115_143022.csv`）
   - 包含三列：账号名称、发推时间、推文内容
   - 可用Excel或其他表格软件打开

2. **文本文件**（如：`x_following_20241115_143022.txt`）
   - 便于阅读的文本格式
   - 包含完整的推文内容

## 时间格式说明

程序支持解析以下时间格式：

- **相对时间**：
  - `55m` → 55分钟前
  - `3h` → 3小时前
  - `2d` → 2天前

- **绝对日期**：
  - `Nov 14` → 当年11月14日
  - `Nov 14, 2023` → 2023年11月14日

## 注意事项

1. **必须使用调试模式启动浏览器**：否则程序无法连接
2. **保持浏览器打开**：程序运行期间不要关闭浏览器
3. **确保已登录**：必须在浏览器中登录X账号
4. **页面刷新**：程序会自动刷新页面以获取最新内容
5. **提取数量**：默认最多提取50条推文，可在代码中修改`max_tweets`参数

## 常见问题

### Q: 提示"连接浏览器失败"
A: 确保已使用调试模式启动Chrome浏览器，并且端口号正确（默认9222）

### Q: 提示"未找到Following页面"
A: 确保在浏览器中已打开X的Following页面，或手动导航到该页面

### Q: 提取不到推文内容
A: 
- 检查是否已登录X账号
- 尝试手动滚动页面加载更多内容
- X的页面结构可能已更新，需要调整选择器

### Q: 时间解析不正确
A: 如果遇到新的时间格式，可以在`parse_time_string`函数中添加支持

## 自定义配置

可以在代码中修改以下参数：

- `debug_port`：Chrome调试端口（默认9222）
- `max_tweets`：最大提取推文数量（默认50）

## 技术说明

- 使用Selenium连接到已存在的Chrome浏览器实例
- 通过Chrome DevTools Protocol进行通信
- 自动识别和解析X的推文结构
- 支持多种时间格式的智能解析

