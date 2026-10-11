"""界面精修的静态自查（B 侧，方向 A 保守精修）。

本脚本只做**静态可验证**的部分，不涉及真实排版：
  1. 设计令牌存在且成对（间距 / 字阶 / 圆角 / 语义色）
  2. 关键前景-背景组合的 WCAG 对比度（正文、次要文字、主按钮、五组语义标签）
  3. 组件六态齐全（默认 / hover / active / focus-visible / disabled / 错误或空）
  4. 不得出现"去掉焦点又没有替代"的规则
  5. 不得出现会撑破 390px 的固定宽度
  6. 尊重 prefers-reduced-motion

**明确不做**：真实布局溢出、真机渲染、视觉美观度判断——那些要在浏览器/真机上做，
属 C 的范围。本脚本给出的是"能自动证明的部分"，不冒充视觉验收。

用法：python tools/check_visual.py [app/static/app.css] [app/templates]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CSS_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "app" / "static" / "app.css"
TPL_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "app" / "templates"

results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name))
    print(("PASS  " if ok else "FAIL  ") + name + (("  — " + detail) if detail else ""))


css = CSS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 工具
def token(name: str) -> str | None:
    """取**最后一条**定义：同规格选择器（两个 :root）下后者在层叠中生效，
    所以实际生效的是覆盖值，检查必须跟着层叠走，否则会拿旧值误判。"""
    matches = re.findall(r"--%s:\s*(#[0-9a-fA-F]{3,6})" % re.escape(name), css)
    return matches[-1] if matches else None


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def luminance(value: str) -> float:
    def channel(raw: int) -> float:
        ratio = raw / 255
        return ratio / 12.92 if ratio <= 0.04045 else ((ratio + 0.055) / 1.055) ** 2.4

    red, green, blue = rgb(value)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(foreground: str, background: str) -> float:
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# ---------------------------------------------------------------- 1. 令牌
print("=" * 92)
print("1) 设计令牌")
for group, names in (
    ("间距阶", ["space-1", "space-2", "space-3", "space-4", "space-5", "space-6"]),
    ("字阶", ["font-xs", "font-sm", "font-base", "font-md", "font-lg", "font-xl", "font-2xl", "font-3xl"]),
    ("圆角", ["radius", "radius-sm", "radius-pill"]),
    ("语义色", ["tone-ok-fg", "tone-ok-bg", "tone-warn-fg", "tone-warn-bg", "tone-info-fg",
                "tone-info-bg", "tone-neutral-fg", "tone-neutral-bg", "tone-danger-fg", "tone-danger-bg"]),
    ("基础色", ["color-text", "color-muted", "color-border", "color-bg", "color-card",
                "color-primary", "color-primary-dark", "color-danger"]),
    ("动效", ["t-fast", "t-base"]),
):
    missing = [n for n in names if not re.search(r"--%s:" % re.escape(n), css)]
    check("%s 令牌齐全（%d 个）" % (group, len(names)), not missing,
          ("缺 " + ", ".join(missing)) if missing else "")

# 间距阶必须是 4 的倍数且递增，避免"随手加一个 10px"
spaces = [int(re.search(r"--space-%d:\s*(\d+)px" % i, css).group(1)) for i in range(1, 7)
          if re.search(r"--space-%d:\s*(\d+)px" % i, css)]
check("间距阶严格递增且都是 4 的倍数",
      len(spaces) == 6 and spaces == sorted(spaces) and all(s % 4 == 0 for s in spaces),
      str(spaces))

check("同一含义的圆角只用一套（控件走 --radius-sm，卡片走 --radius）",
      "--radius-sm: 6px" in css and "border-radius: 6px" not in css,
      "残留硬编码 6px 圆角 %d 处" % css.count("border-radius: 6px"))

# ---------------------------------------------------------------- 2. 对比度
print("=" * 92)
print("2) WCAG 对比度（AA 正文要求 ≥4.5:1）")
pairs = [
    ("正文 / 卡片", "color-text", "color-card", 4.5),
    ("正文 / 页面底", "color-text", "color-bg", 4.5),
    ("次要文字 / 卡片", "color-muted", "color-card", 4.5),
    ("次要文字 / 页面底", "color-muted", "color-bg", 4.5),
    ("主按钮文字 / 主色", None, None, 4.5),
    ("成功标签", "tone-ok-fg", "tone-ok-bg", 4.5),
    ("警示标签", "tone-warn-fg", "tone-warn-bg", 4.5),
    ("信息标签", "tone-info-fg", "tone-info-bg", 4.5),
    ("中性标签", "tone-neutral-fg", "tone-neutral-bg", 4.5),
    ("危险标签", "tone-danger-fg", "tone-danger-bg", 4.5),
    ("警示提示条文字 / 底", "color-text", "tone-warn-bg", 4.5),
    ("错误提示条文字 / 底", "color-danger", "tone-danger-bg", 4.5),
]
for label, fg_name, bg_name, threshold in pairs:
    if fg_name is None:                      # 主按钮：白字 on 主色
        foreground, background = "#ffffff", token("color-primary")
    else:
        foreground, background = token(fg_name), token(bg_name)
    if not foreground or not background:
        check("对比度：%s" % label, False, "取不到颜色值")
        continue
    ratio = contrast(foreground, background)
    check("对比度：%-14s %s on %s = %.2f:1" % (label, foreground, background, ratio),
          ratio >= threshold, "低于 %.1f" % threshold if ratio < threshold else "")

# ---------------------------------------------------------------- 3. 六态
print("=" * 92)
print("3) 组件六态（静态存在性）")
states = [
    ("按钮 hover（主）", r"\.button-primary:hover"),
    ("按钮 hover（次）", r"\.button-ghost:hover"),
    ("按钮 active", r"\.button:active"),
    ("按钮 disabled", r"\.button:disabled"),
    ("输入 focus", r"input\[type=\"text\"\]:focus"),
    ("键盘焦点环 focus-visible", r":focus-visible\s*\{[^}]*outline"),
    ("错误态 .field-errors", r"\.field-errors\s*\{"),
    ("错误态 .form-status.is-error", r"\.form-status\.is-error"),
    ("成功态 .form-status.is-ok", r"\.form-status\.is-ok"),
    ("加载中/进行中样式", r"\.tool-status-running|\.tag-running"),
    ("禁用视觉不脏（用中性底）", r"\.button\[disabled\][^}]*tone-neutral-bg"),
]
for label, pattern in states:
    check("六态：%s" % label, re.search(pattern, css, re.S) is not None)

template_blob = "\n".join(p.read_text(encoding="utf-8") for p in sorted(TPL_DIR.glob("*.html")))
for label, needle in (
    ("空状态：报告页", 'data-testid="report-empty-card"'),
    ("空状态：项目列表", 'data-testid="project-empty"'),
    ("错误容器：字段级", 'data-testid="field-errors"'),
    ("状态输出：aria-live", 'aria-live="polite"'),
):
    check("六态：%s" % label, needle in template_blob)

# ---------------------------------------------------------------- 4. 焦点不可去除
print("=" * 92)
print("4) 焦点可见性")
bad = []
for match in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
    selector, body = match.group(1).strip(), match.group(2)
    if re.search(r"outline:\s*(none|0)\b", body) and "box-shadow" not in body:
        bad.append(selector.splitlines()[-1].strip())
check("不存在「去掉 outline 又没有替代」的规则", not bad, "; ".join(bad[:3]))
check("覆盖了所有可聚焦元素（input/textarea/select/button/a）",
      all(re.search(r"%s:focus-visible" % sel, css)
          for sel in ("input", "textarea", "select", "button", "a")))
check("窄屏可点区域 ≥44px（min-height 未被调小）",
      re.search(r"\.button\s*\{[^}]*min-height:\s*44px", css, re.S) is not None
      or re.search(r"min-height:\s*44px", css) is not None)

# ---------------------------------------------------------------- 5. 窄屏固定宽度
print("=" * 92)
print("5) 390px 窄屏风险（静态）")
wide = [m.group(0).strip() for m in re.finditer(r"(?:^|[;{\s])(min-)?width:\s*(\d{3,})px", css)
        if not m.group(1) and int(m.group(2)) > 390]
check("没有 >390px 的固定宽度（max-width 除外）", not wide, "; ".join(wide[:3]))
check("存在 430px 窄屏断点", "@media (max-width: 430px)" in css)
check("长标识（UUID/链接/时间）允许断行",
      re.search(r"\.mono\s*\{[^}]*word-break:\s*break-all", css, re.S) is not None
      or re.search(r"\.mono\s*\{[^}]*overflow-wrap", css, re.S) is not None)
check("body 有横向溢出兜底", re.search(r"body\s*\{[^}]*overflow-x:\s*hidden", css, re.S) is not None)

# ---------------------------------------------------------------- 6. 动效偏好
print("=" * 92)
print("6) 动效与偏好")
check("声明了 prefers-reduced-motion 降级", "prefers-reduced-motion" in css)
check("交互反馈用了令牌时长（不是随手写死）",
      css.count("var(--t-fast)") + css.count("var(--t-base)") >= 8,
      "使用 %d 处" % (css.count("var(--t-fast)") + css.count("var(--t-base)")))
check("没有引入外部资源（离线可用）",
      not re.search(r"@import|https?://[^)\"' ]+\.(css|js|woff2?|png|svg)", css))

# ---------------------------------------------------------------- 7. 九种标签
print("=" * 92)
print("7) 九种语义标签")
tags = ["draft", "planned", "collecting", "reviewed", "real", "test", "running", "succeeded", "failed"]
colors = {}
for tag in tags:
    # 同样按层叠取最后一条：覆盖规则才是实际生效的
    found_rules = re.findall(r"\.tag-%s\s*\{([^}]*)\}" % tag, css)
    body = found_rules[-1] if found_rules else ""
    ok = "--tone-" in body
    check("标签 .tag-%-11s 用语义令牌" % tag, ok, "" if ok else (body.strip()[:60] or "找不到规则"))
    match = re.search(r"var\((--tone-[a-z]+-(?:fg|bg))\)", body)
    if match:
        colors[tag] = match.group(1)
check("九种标签都能对上令牌", len(colors) == 9, "%d/9" % len(colors))
check("语义分组合理（成功 3 种同色 / 警示 3 种同色）",
      len(colors) == 9
      and len({colors.get("reviewed"), colors.get("real"), colors.get("succeeded")}) == 1
      and len({colors.get("collecting"), colors.get("test"), colors.get("running")}) == 1,
      str(colors))

failed = [name for ok, name in results if not ok]
print("=" * 92)
print("共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
for name in failed:
    print("  失败：", name)
print("-" * 92)
print("本脚本只覆盖静态可验证项；真实排版溢出、真机渲染与美观度判断需在浏览器/真机上做（C 的范围）。")
sys.exit(1 if failed else 0)
