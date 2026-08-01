@echo off
echo 正在升级pandas到2.2.3版本...
pip install pandas==2.2.3 --upgrade
echo.
echo 升级完成！正在验证版本...
python -c "import pandas as pd; print('当前pandas版本:', pd.__version__)"
pause

