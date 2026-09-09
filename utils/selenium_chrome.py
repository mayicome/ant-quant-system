# -*- coding: utf-8 -*-
"""Selenium Chrome 启动：优先便携版，否则系统 Chrome + Selenium Manager。"""
from __future__ import annotations

import os
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


_PORTABLE_CHROME = r"D:\download\chrome-win64\chrome-win64\chrome.exe"
_PORTABLE_DRIVER = r"D:\download\chromedriver-win64\chromedriver.exe"

_SYSTEM_CHROME_CANDIDATES = (
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def find_dc_path(d_path: str, c_path: Optional[str] = None) -> Optional[str]:
    """D 盘优先，否则同路径换到 C 盘；都不存在返回 None。"""
    if c_path is None:
        if d_path.startswith("D:"):
            c_path = "C:" + d_path[2:]
        elif d_path.startswith("d:"):
            c_path = "c:" + d_path[2:]
        else:
            c_path = d_path
    if os.path.isfile(d_path):
        return d_path
    if os.path.isfile(c_path):
        return c_path
    return None


def apply_chrome_proxy_options(chrome_options: Options) -> None:
    """默认直连，避免继承失效的系统代理。"""
    use_system_proxy = os.environ.get("SELENIUM_USE_SYSTEM_PROXY", "").strip().lower()
    if use_system_proxy in ("1", "true", "yes"):
        print("使用系统代理（SELENIUM_USE_SYSTEM_PROXY=1）")
        return
    chrome_options.add_argument("--no-proxy-server")
    chrome_options.add_argument("--proxy-bypass-list=*")
    print("已禁用 Chrome 系统代理（直连）；如需走代理请设置 SELENIUM_USE_SYSTEM_PROXY=1")


def resolve_chrome_binary() -> Optional[str]:
    portable = find_dc_path(_PORTABLE_CHROME)
    if portable:
        print(f"使用便携 Chrome: {portable}")
        return portable
    env = (os.environ.get("CHROME_BINARY") or os.environ.get("SELENIUM_CHROME_BINARY") or "").strip()
    if env and os.path.isfile(env):
        print(f"使用环境变量 Chrome: {env}")
        return env
    for path in _SYSTEM_CHROME_CANDIDATES:
        if path and os.path.isfile(path):
            print(f"使用系统 Chrome: {path}")
            return path
    print(
        "未找到便携/系统 Chrome；将交由 Selenium 自动探测。"
        "也可设置 CHROME_BINARY=chrome.exe 完整路径。"
    )
    return None


def resolve_chromedriver() -> Optional[str]:
    portable = find_dc_path(_PORTABLE_DRIVER)
    if portable:
        print(f"使用便携 ChromeDriver: {portable}")
        return portable
    env = (os.environ.get("CHROMEDRIVER") or os.environ.get("SELENIUM_CHROMEDRIVER") or "").strip()
    if env and os.path.isfile(env):
        print(f"使用环境变量 ChromeDriver: {env}")
        return env
    print("未找到便携 ChromeDriver；交由 Selenium Manager 自动匹配驱动。")
    return None


def create_chrome_driver(*, headless: bool = False) -> webdriver.Chrome:
    """创建 Chrome WebDriver：便携路径优先，否则系统 Chrome + 自动驱动。"""
    chrome_options = Options()
    binary = resolve_chrome_binary()
    if binary:
        chrome_options.binary_location = binary
    apply_chrome_proxy_options(chrome_options)
    if headless:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver_path = resolve_chromedriver()
    if driver_path:
        service = Service(executable_path=driver_path)
    else:
        service = Service()
    return webdriver.Chrome(service=service, options=chrome_options)
