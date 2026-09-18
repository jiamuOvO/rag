"""Diagnose the configured credential WITHOUT revealing it.

Prints only shape information -- length, whitespace, quote characters, character class.
Never prints the value itself, never writes it anywhere.

Usage: python -B tests/biomass_furan/check_key.py
"""
from __future__ import annotations

import os
import string

HEX = set(string.hexdigits)


def classify(value: str) -> str:
    if all(c in HEX for c in value):
        return "纯十六进制字符"
    if all(c.isalnum() or c in "-_." for c in value):
        return "字母数字（含 - _ .）"
    if all(32 <= ord(c) < 127 for c in value):
        return "可打印 ASCII（含符号）"
    return "含非 ASCII 字符"


def main() -> None:
    for name in ("RAG_CHAT_API_KEY", "RAG_EMBEDDING_API_KEY"):
        raw = os.getenv(name)
        print(f"--- {name}")
        if raw is None:
            print("    未设置（环境变量不存在）")
            continue
        if raw == "":
            print("    已设置，但值为空字符串")
            continue
        print(f"    长度            : {len(raw)}")
        print(f"    首字符是空白    : {raw[0].isspace()}")
        print(f"    末字符是空白    : {raw[-1].isspace()}")
        print(f"    含引号          : {chr(34) in raw or chr(39) in raw}")
        print(f"    以 Bearer 开头  : {raw.lower().startswith('bearer')}")
        print(f"    含换行/制表符   : {any(c in raw for c in chr(10) + chr(13) + chr(9))}")
        print(f"    字符类别        : {classify(raw)}")
        print(f"    去除首尾空白后长度是否变化: {len(raw.strip()) != len(raw)}")

    print()
    print("提示：两个密钥分别属于不同服务，不能混用。")
    print("  聊天模型服务 : 183.162.245.47:11888")
    print("  向量模型服务 : 192.168.31.1:7001")
    print("带空白的密钥是最常见原因；其次是把两个密钥填反了。")


if __name__ == "__main__":
    main()
