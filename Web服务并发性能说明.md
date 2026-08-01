# Web服务并发性能说明

## 当前状态

### 并发限制分析

**当前配置：**
- ✅ **已启用多线程** (`threaded=True`)
- ⚠️ **数据更新限制**：使用锁机制，同时只能有一个数据更新操作
- ⚠️ **Chrome资源限制**：每个数据更新会启动Chrome实例，消耗大量内存（200MB+）

### 实际并发能力

**Web请求并发：**
- **理论值**：Flask开发服务器（Werkzeug）可以处理 **几十到上百个并发请求**
- **实际值**：受以下因素限制，通常为 **5-20个并发请求**

**限制因素：**

1. **数据更新锁** (`_data_cache['lock']`)
   - 多个请求同时触发数据更新时，会被串行化
   - 不会阻塞读取缓存数据的请求

2. **Chrome资源消耗**
   - 每个数据更新启动一个Chrome实例
   - 单个Chrome实例占用：200-500MB内存
   - 系统内存限制会限制并发数

3. **Flask开发服务器限制**
   - 开发服务器不适合高并发生产环境
   - 建议使用生产级WSGI服务器（Gunicorn、uWSGI等）

---

## 性能优化建议

### 方案1：使用生产级WSGI服务器（推荐）

**使用Gunicorn（Linux/macOS）：**

```bash
# 安装Gunicorn
pip install gunicorn

# 启动服务（4个工作进程，每个进程可处理多个请求）
gunicorn -w 4 -b 0.0.0.0:5000 limit_up_sector_monitor_web:app

# 或使用更多工作进程（根据CPU核心数调整）
gunicorn -w 8 -b 0.0.0.0:5000 --threads 4 limit_up_sector_monitor_web:app
```

**使用Waitress（跨平台，Windows可用）：**

```bash
# 安装Waitress
pip install waitress

# 启动服务
waitress-serve --host=0.0.0.0 --port=5000 limit_up_sector_monitor_web:app
```

**预期提升：**
- 并发能力：**50-200+ 并发请求**
- 稳定性：显著提升
- 资源利用：更高效

---

### 方案2：优化数据更新机制

**当前问题：**
- 数据更新使用全局锁，串行化所有更新请求
- 即使多个请求同时到达，也只会执行一次更新

**优化建议：**

```python
# 在 update_data() 函数中添加请求队列
# 多个并发请求时，只执行一次更新，其他请求等待结果
import threading
from queue import Queue

_update_queue = Queue()
_update_in_progress = threading.Lock()
_update_result = None
_update_result_lock = threading.Lock()

def update_data_optimized():
    """优化的数据更新：多个并发请求共享一次更新"""
    global _update_result
    
    # 检查是否有正在进行的更新
    if _update_in_progress.locked():
        # 等待更新完成
        with _update_result_lock:
            return _update_result
    
    # 执行更新
    with _update_in_progress:
        result = update_data()
        with _update_result_lock:
            _update_result = result
        return result
```

---

### 方案3：Chrome实例池（高级）

**问题：**
- 每次数据更新都启动新的Chrome实例，开销大
- 可以考虑复用Chrome实例（需要处理会话隔离）

**实现思路：**
```python
from queue import Queue
import threading

class ChromePool:
    def __init__(self, pool_size=2):
        self.pool = Queue(maxsize=pool_size)
        self.lock = threading.Lock()
        
    def get_driver(self):
        """获取Chrome驱动"""
        try:
            return self.pool.get_nowait()
        except:
            # 创建新的驱动
            return create_chrome_driver()
    
    def return_driver(self, driver):
        """归还Chrome驱动"""
        try:
            self.pool.put_nowait(driver)
        except:
            # 池已满，关闭驱动
            driver.quit()
```

**注意：** Chrome实例复用需要处理会话隔离，实现较复杂。

---

## 当前并发能力测试

### 测试方法

```python
# 使用Apache Bench (ab) 测试
ab -n 100 -c 10 http://localhost:5000/api/data

# 或使用wrk
wrk -t4 -c100 -d30s http://localhost:5000/api/data
```

### 预期结果

**当前配置（Flask开发服务器 + threaded=True）：**
- 10并发：✅ 正常
- 20并发：✅ 正常（可能有轻微延迟）
- 50并发：⚠️ 可能出现超时
- 100并发：❌ 可能失败

**使用Gunicorn后：**
- 10并发：✅ 正常
- 50并发：✅ 正常
- 100并发：✅ 正常
- 200并发：⚠️ 取决于服务器配置

---

## 总结

### 当前状态
- ✅ **已启用多线程支持** (`threaded=True`)
- ⚠️ **并发限制**：约 **5-20个并发请求**（取决于系统资源）
- ⚠️ **瓶颈**：Chrome资源消耗、数据更新锁

### 改进建议
1. **短期**：保持当前配置，适用于小规模使用（<20并发）
2. **中期**：使用Waitress或Gunicorn，提升到 **50-200并发**
3. **长期**：考虑Chrome实例池、数据缓存优化等高级方案

### 推荐配置

**小规模使用（<20用户）：**
```python
app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
```

**中等规模（20-100用户）：**
```bash
waitress-serve --host=0.0.0.0 --port=5000 --threads=8 limit_up_sector_monitor_web:app
```

**大规模（100+用户）：**
```bash
gunicorn -w 4 --threads 4 -b 0.0.0.0:5000 limit_up_sector_monitor_web:app
```

---

**更新时间**：2026-01-23
**当前版本**：已启用 `threaded=True`

