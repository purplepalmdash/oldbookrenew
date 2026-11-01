# 调参手册（S3 白化 + S5 几何，均以基准页对样张校准）

> 铁律：**基准页（用户认可前）只做一页**。调定后把参数冻结进
> `work/params.json`，批量阶段禁止再改。改了就重新对样张。

## 1. S3 白化参数（s3_whiten.py DEFAULT_PARAMS）

### 目标
把基准页插图白化结果与样张（example_pdf 对应区域）视觉一致：
背景纯白、无灰/黄残留、网点变平滑但保留灰调层次（木刻深色填充、
天空渐变）、线稿近黑锐利。

### 工具
```sh
cd <project>/src
python s3_whiten.py 4                       # 白化基准页，打印像素统计
python s3_whiten.py --pxstats work/pages/004_img0.png        # 输出统计
python s3_whiten.py --pxstats <样张裁剪.png> [x0 y0 x1 y1]   # 样张对应区域统计
# 样张渲染：pdftoppm -png -r 200 example.pdf work/example 后裁对应插图区域
```
stats 含义：`min/p5/p50/p95/max` + `frac255`（纯白占比，省墨关键）。
对齐方法：先对"背景区"（纯纸面小窗口）要求 `p5≈p50≈255, frac255→1`；
再对"整图"要求 p50/p95 与样张同区间（灰调层次一致）。

### 参数逐项
| 参数 | 作用 | 调法 |
|---|---|---|
| `lut` | 区域灰度 → 输出灰度（分段线性锚点，单调）。**纸张必须映到 1.0**（省墨） | 锚点含义：`(in=区域平均灰度, out=输出)`。0.93–1.0→0.99–1.0（纸/浅色网点→白）；0.75→~0.6（中网点）；0.68→~0.45（木刻深填）；≤0.4→0.02–0.15（墨芯）。某灰调偏亮/偏暗就挪对应锚点 |
| `max_kernel` | MaxFilter 估计本地纸面（光照不均/大面积泛黄压平） | 背景不平→加大；深色填充被"提亮"（kernel 跨进暗部）→减小。保持奇数 |
| `tone_radius` | BoxBlur 半径：网点/纹理的"区域均值" | 网点颗粒残留→加大；暗部细节糊掉→减小 |
| `dark_cut` | flat < dark_cut 的像素按"墨芯"处理（纹理增益高） | 线稿断→降低阈值让细线进墨芯通道 |
| `tex_gain_dark` / `tex_gain_light` | 墨芯/亮部局部纹理增益 | 线稿不锐→加大 dark；网点感太强→减小 light |
| `paper_seed` | 与图边连通且亮度≥seed 的区域强制 255 | 图内大片纸面残留灰→调小（如 0.975→0.95）；图内浅色天空被误涂白→调大 |
| `remove_red` / `remove_blue` | 彩色预通道：抹掉扫描附加的红/蓝墨水（声明、水印、蓝字） | 全书级放 `whiten_params`，单页放 `page_whiten`：`{"2": {"remove_red": true}}` |

### 冻结
```sh
cd <project>/src
python -c "from bookcfg import save_params; save_params({...最终参数...})"
```
写 `work/params.json`。之后 s3 的优先级：代码默认 < book.json `whiten_params`
< **params.json（冻结）** < `page_whiten` 单页覆盖。

## 2. S5 几何（s5_typeset.py 顶部常量）

| 常量 | 作用 | 调法 |
|---|---|---|
| `BASE_OFF` | 基线在行 bbox 底边之下（em 分数） | 看 `NNN_out.png` 与样张对齐情况，±0.02 步进；只动基准页调，调完别忘记录 |
| `VL_ADV` | 竖排字距（em） | 竖排页（书名页）对照样张 |
| `font_size` | = 该行行高中位数 × 全局 scale（代码自动） | 个别行大小不同（版权页双栏）→ JSON 行加 `fontsize_src` 覆盖 |

全局缩放 `work/scale.json`：所有页 content_bbox 能放进版心的最大公共 scale。
**页集变化（补页/删页）后删掉重算**，否则排版偏大/溢出。

## 3. 调参节奏（基准页循环）

1. 跑 s2 → 看 `_dbg.png` 框线对不对（图框红、行蓝、token 紫、细线绿）
2. 跑 s3 → `--pxstats` 对样张，改 LUT/参数
3. 视觉对比（agent 并排看图）：纯白背景？灰调层次？线稿？
4. S4 填字（哪怕只基准页一行也行，先用 est_chars 校验流程）
5. 跑 s5 → `_out.png` 对样张：行数/断行/首尾字/位置
6. 全过 → 请用户验收 → 冻结 → 才许批量
