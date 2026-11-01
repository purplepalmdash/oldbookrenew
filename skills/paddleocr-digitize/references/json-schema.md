# 数据文件 schema（下游脚本的契约）

坐标基准：**S1 渲染图的像素坐标**（2x 渲染时 2048×1536）。全书统一，
S5 重建时一次性映射到 pt（`S=595/render_H, OX=(842-render_W*S)/2`）。

## `data/pageNNN.json`（S2 产出，S4 可能更新）

```json
{
  "page": 135,                      // 1-based 源 PDF 页号
  "render_scale": 2.0,
  "width": 2048, "height": 1536,    // 渲染图尺寸
  "deskew_deg": -0.42,              // 可选：S1.5 整页摆正旋角（仅旋转过的页有）
  "layout": [
    {"label": "image", "score": 0.99, "bbox": [243.0, 153.5, 1755.0, 1138.0]},
    {"label": "text",  "score": 0.97, "bbox": [150.0, 1210.0, 1900.0, 1480.0]}
  ],
  "ocr": {
    "text": "132这时候，…\n也含着热泪说：…",
    "confidence": 0.99,
    "lines": [
      {
        "text": "132这时候，阿雅牵着白马来到了这里，看到这种情景，",
        "score": 1.0,
        "quad": [[176,1220],[1853,1220],[1853,1317],[176,1317]],
        "start_point": [176, 1220],   // 行起始（左上）
        "end_point":   [1853, 1317]   // 行结束（右下）
      }
    ],
    "dropped_in_image_lines": ["DW", "0U0F"],   // 落在插图内的垃圾行（留痕）
    "rescue": {                               // 仅 S4 救援过的页有
      "frame_bottom": 1180,
      "old_low_score_lines": [{"text": "具房局路路路…", "score": 0.43}],
      "new_lines": 3
    }
  },
  "timings": {"layout_s": 2.1, "ocr_s": 5.4}
}
```

- `quad` 恒为 **4 角点** `[[x,y]×4]`（S2 已把扁平 `[x1,y1,x2,y2]` 归一化）。
- `lines` 已剔除"中心点落在 image bbox 内(±4px)"的行；
  **S5 不得再用 layout bbox 二次过滤**（layout 框比真实插图框松，
  会误杀紧贴框下的字幕行——本项目 6 页首行曾因此丢失）。
- 页码常并入正文字（`"53 黑鬼怪说罢…"`），属正常。
- S4 救援行带 `"rescued": true`。

## `work/images/pageNNN.json`（S3 产出）

```json
{
  "page": 135,
  "images": [
    {
      "index": 0,
      "image_bbox_layout": [243.0, 153.5, 1755.0, 1138.0],  // 来源 layout 框
      "frame_center_page": [1022.7, 612.5],   // 边框中心（页坐标）← S5 放图用
      "frame_w": 1758, "frame_h": 1142,       // 对齐后图像尺寸（px）
      "angle_deg": 0.543,                     // 边框顶边倾角（仅元数据）
      "aligned_shape": [1142, 1758]           // [H, W]
    }
  ]
}
```

- 插图文件 `work/images/pageNNN_{index}.png`（已 Otsu 二值化）。
- 无框插图回退时 `angle_deg=0`，`frame_center_page` = layout 框中心。
- 纯文字页 `images: []`。

## `data/pageNNN_special.json`（特殊页手工几何，如竖排扉页）

```json
{
  "page": 2,
  "source": "rotated OCR (90° CW) + strip OCR, MCP paddleocr",
  "image": { /* 同 images/pageNNN.json 的 image 结构 */ },
  "vertical_columns": [
    {"text": "龙子太郎", "x1": 1546, "x2": 2010, "y1": 39, "y2": 1279,
     "n_chars": 4, "font_px": 310.0}
  ],
  "horizontal_lines": [
    {"text": "白云山扫描制作",
     "quad": [[713,1255],[1255,1255],[1255,1310],[713,1310]],
     "start_point": [713,1255], "end_point": [1255,1310]},
    {"text": "本书仅供个人欣赏…", "quad": [[419,1372],…], "color": "red"}
  ]
}
```

- `vertical_columns`：字符**正立、自上而下**逐字堆叠（不是旋转 90° 的文字）；
  字号 = `min(x2-x1, (y2-y1)/n_chars)`。
- `"color": "red"` 的行用独立 TextWriter + `write_text(page, color=(…))`。
- book.json 里 `special_pages: {"2": "page002_special.json"}` 登记。

## `book.json`（书特定参数，`<out>/book.json`）

```json
{
  "keep_pages": [1, 139],
  "special_pages": {"2": "page002_special.json"},
  "text_only_pages": [3, 138],
  "drop_pages": [63],                       // 可选：整页丢弃（纯水印广告尾等）
  "image_overrides": {                       // 可选：整页替换（去水印封面/封底）
    "1": "work/images/override_p001.png",
    "62": "work/images/override_p062.png"
  },
  "out_name": "书名字_数字化.pdf",
  "title": "书名", "author": "…", "subject": "出版社 年份",
  "keep_sizes": {"1": [1024, 768], "139": [240, 192]}   // 可选：QA 尺寸断言
}
```

缺省值：无 keep/special/text_only/drop/override，out_name 取 `<out>/数字化.pdf`。

- `drop_pages` 的页不进成品，且 mcp_batch/test_page 都跳过（页数断言按
  源-dropped 计）。
- `image_overrides` 的页按页面 rect 整幅插入处理图（不 OCR、无文字层），
  mcp_batch 自动跳过识别。
