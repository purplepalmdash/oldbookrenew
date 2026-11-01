# Scribus 路线参考文件（S5'）

`scripts/scribus_build.py`（主技能 scripts/ 下）执行 **plan JSON**；本目录是
**两本真实书的具体规划器**与配套工具，供写新规划器时参考：

| 文件 | 说明 |
|---|---|
| `plan_build.py` | 孙膑和庞涓（114 页，数字扫描）：每页统一字号（宽/有效em 中位数）、`true_em` 字体实测宽、帧宽 = max(quad, 实测宽×1.04)、宽间距行逐字 frame |
| `plan_build_liuxiu.py` | 刘秀上册（154 页）：在上一版基础上增加**两端对齐行**处理（纯 CJK 逐字等宽 slot / 含数字行按 advance 加权 slot，规避 Scribus 数字-CJK 词间距伪影）、短尾行宽度拟合字号、**版权页单元格表格重建**（字号=实测墨高/0.88、横线灰度检测） |
| `overflow_check.py` | 帧级折行检测：锚定 PDF 行，判断它是数据行真前缀且剩余字符出现在下一行行首（行数 QA 抓不到的末字折行） |
| `strip_all.py` | 全书正文页条带并行重识别（4 worker，MCP 直连），权威替换 det 漏检/碎片化行 |
| `strip_fix.py` | 书特定词表订正（同音近形字：孙膑→孙宾、美街→美术…），必须放在所有识别之后、规划器之前 |

**注意**：`plan_build*.py` / `strip_*.py` 是**书特定脚本**，文件头部的
`OUT` / `SRC` / 字体路径等是原项目机器的具体配置（已做 `path/to/` 脱敏），
移植到新书时先改这些常量（或改造成 argparse）。`overflow_check.py` 同理
（`OUT` 常量）。其余逻辑与书无关。

plan op 格式与顺序铁律见 `scripts/scribus_build.py` 头部 docstring；
完整路线心得（常驻 bridge、image 三件套、set_text 顺序、orientation 坑等）
见主技能 `SKILL.md` 的 S5' 一节。
