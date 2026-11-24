"""解析通用工具。"""
import re

_SKIP_TOP = re.compile(
    r"^(#|!|return$|end$|exit$|quit$|Building configuration|Current configuration|version |"
    r"\s*$|<.*>|\[.*\]|.*[>#]\s*(display|show|dis)\b)", re.I)


def split_blocks(text):
    """切分成 [(header, [(depth, line), ...]), ...]。

    顶格的行为一个配置段的开头，缩进的行为该段的子命令。
    """
    blocks = []
    cur = None
    cur_global = False   # 当前行是否为 '#' 之后缩进一格的全局命令（华三/华为风格）
    global_indent = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if indent == 0:
            cur_global = False
            if _SKIP_TOP.match(line):
                cur = None
                continue
            cur = (stripped, [])
            blocks.append(cur)
        else:
            if stripped in ("#", "!"):
                cur = None
                continue
            if cur is None or (cur_global and indent <= global_indent):
                # 华三/华为: '#' 后面缩进一格的行是全局命令
                if _SKIP_TOP.match(stripped):
                    continue
                cur = (stripped, [])
                cur_global = True
                global_indent = indent
                blocks.append(cur)
                continue
            cur[1].append((indent, stripped))
    return blocks


def block_text(header, subs):
    return [header] + [" " * d + s for d, s in subs]


def unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1]
    return s
