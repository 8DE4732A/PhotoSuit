# PhotoSuit

基于 EXIF 信息的图像边框与水印合成工具。读取照片的相机参数，通过 SVG + Jinja2 模板引擎动态渲染带有厂商 Logo、拍摄参数的相框边框。

## 实现原理

### 架构

系统采用 5 阶段处理管线：

```
Parse → Calculate → Render → Rasterize → Composite
```

1. **Parse** — 使用 `exifread` 读取图片 EXIF 头部信息（不加载像素），通过 Pillow 获取图片尺寸
2. **Calculate** — 将 EXIF 原始数据归一化：统一厂商名称映射（如 `"CANON INC."` → `"Canon"`）、格式化参数（`"50"` → `"50mm"`）、将品牌 Logo SVG 编码为 Base64 data URI
3. **Render** — Jinja2 加载 SVG 模板文件，注入归一化后的完整 Context（EXIF、布局尺寸、Logo 资源、用户参数），输出一段填满实际数据的纯 SVG 字符串
4. **Rasterize** — `resvg`（Rust SVG 渲染库的 Python 绑定）将 SVG 栅格化为透明背景 PNG，支持系统字体加载
5. **Composite** — Pillow 创建背景画布，将原始照片和相框层按坐标合成，使用 `piexif` 回写原始 EXIF 到输出文件

### 模板系统

模板采用插件式设计，每个模板是一个独立目录：

```
app/templates/<template_id>/
├── config.json      # 元数据与可调参数定义
└── template.svg     # Jinja2 + SVG 混合模板
```

- `config.json` 声明模板暴露的可调参数（边距、背景色、是否显示 Logo 等），带默认值
- `template.svg` 使用 Jinja2 语法控制 SVG DOM 生成，运行时注入 EXIF 数据、Logo 资源、布局尺寸

新增模板只需创建一个新目录，编写对应的 `config.json` 和 `template.svg`，无需修改代码。

### 技术选型

| 组件 | 选型 | 职责 |
|------|------|------|
| EXIF 读取 | exifread | 快速读取图片头部，不加载像素 |
| EXIF 回写 | piexif | 将原始 EXIF 写入输出图片 |
| 模板引擎 | Jinja2 | SVG 模板数据注入与逻辑控制 |
| SVG 栅格化 | resvg (Rust) | 高保真 SVG → PNG 转换，支持系统字体 |
| 图像合成 | Pillow | 画布创建、图层叠加、JPEG 输出 |
| CLI | Typer | 命令行界面与参数解析 |

## 优缺点

### 优点

- **模板驱动**：排版完全由 SVG 模板控制，新增样式无需改代码
- **矢量渲染**：Logo 和文字通过 SVG 矢量渲染，在任意分辨率下保持清晰
- **EXIF 保留**：输出图片保留原始 EXIF 信息
- **品牌覆盖广**：内置 23 个相机/手机品牌 Logo（Canon、Nikon、Sony、Apple、Fujifilm、Leica、Hasselblad、DJI、Huawei、Xiaomi 等）
- **无系统依赖**：`resvg` 作为纯 Python wheel 分发（内含 Rust 编译产物），无需安装 cairo 等系统库
- **参数可配置**：边距、背景色、字体颜色、信息栏高度等均可通过 CLI 参数覆盖

### 缺点

- **字体依赖系统**：resvg 通过 `load_system_fonts()` 加载本地字体，不同操作系统可能渲染出不同字体效果
- **SVG 子集支持**：resvg 不支持完整 SVG 规范（如 CSS 动画、`foreignObject` 等），模板编写需注意兼容性
- **仅支持 JPEG 输出**：当前合成阶段固定输出 JPEG 格式

## 使用方法

### 安装

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/) 包管理器：

```bash
uv sync
```

### 命令

#### 处理单张图片

```bash
uv run python -m app.cli process <图片路径> -o <输出路径> -t <模板ID>
```

示例：

```bash
uv run python -m app.cli process photo.jpg -o output.jpg -t default_white
```

#### 批量处理

```bash
uv run python -m app.cli batch <输入目录> -o <输出目录> -t <模板ID>
```

示例：

```bash
uv run python -m app.cli batch ./photos -o ./output -t default_white
```

#### 使用外置模板目录

所有处理命令均支持 `--templates-dir` 选项，可指定内置模板目录以外的模板路径：

```bash
# 使用外部模板处理图片
uv run python -m app.cli process photo.jpg -o output.jpg -t my_custom \
  --templates-dir /path/to/my_templates

# 列出外部目录中的模板
uv run python -m app.cli templates --templates-dir /path/to/my_templates

# 批量处理使用外部模板
uv run python -m app.cli batch ./photos -o ./output -t my_custom \
  --templates-dir /path/to/my_templates
```

#### 查看图片 EXIF 信息

```bash
uv run python -m app.cli info <图片路径>
```

输出示例：

```
File: photo.jpg
Dimensions: 4000×3000
Make: Canon
Model: EOS R5
Lens: RF 50mm F1.2L USM
Focal Length: 50mm
Aperture: f/1.8
Exposure: 1/250s
ISO: ISO 100
Date: 2024:06:15 14:30:00
```

#### 列出可用模板

```bash
uv run python -m app.cli templates
```

#### 自定义模板参数

通过 `--prop key=value` 覆盖模板默认值：

```bash
uv run python -m app.cli process photo.jpg -o output.jpg -t default_white \
  --prop border_padding=0.08 \
  --prop bg_color=#F5F5F5 \
  --prop show_logo=false
```

#### 启动模板设计器

模板设计器提供可视化的模板创建与编辑体验，支持 SVG 代码编辑、参数配置、实时预览：

```bash
# 启动设计器（使用内置模板目录）
uv run python -m app.cli designer

# 启动设计器（使用外置模板目录）
uv run python -m app.cli designer --templates-dir /path/to/my_templates

# 也可直接运行模块
uv run python -m app.designer
```

设计器也可通过主 GUI 的"工具 -> 模板设计器"菜单打开。

#### 启动 GUI

```bash
uv run python -m app.gui
```

GUI 支持在右侧面板选择外置模板目录，选择后模板列表会自动更新。

### 运行测试

```bash
uv run pytest tests/ -v
```

## 自定义模板开发

有两种方式开发模板：

1. **使用模板设计器**（推荐）— 通过可视化工具创建、编辑、实时预览模板
2. **手动创建文件** — 直接在模板目录下创建 `config.json` 和 `template.svg`

模板可以放在内置目录 `app/templates/` 下，也可以放在任意外部目录中通过 `--templates-dir` 选项指定。

### 使用模板设计器

启动设计器后，界面分为三栏：

- **左侧** — 模板列表、参数测试控件、示例图片选择
- **中央** — SVG 代码编辑器（带语法高亮）+ 实时预览
- **右侧** — 配置编辑器（模板 ID、名称、描述）+ 参数定义表格

工作流程：

1. 点击"新建"创建模板（输入模板 ID）
2. 在右侧配置编辑器填写名称、描述，添加参数定义
3. 在中央编辑器编写 SVG 模板代码
4. 选择一张示例图片，实时预览渲染效果
5. 调整左侧参数测试控件查看不同参数下的效果
6. Ctrl+S 保存

### 手动创建模板

只需创建一个新目录并编写两个文件，即可扩展自己的模板，无需修改任何代码。

### 第一步：创建目录

在 `app/templates/` 下新建目录，目录名即为模板 ID：

```
app/templates/my_template/
├── config.json
└── template.svg
```

### 第二步：编写 config.json

`config.json` 声明模板的元信息和用户可调参数：

```json
{
  "id": "my_template",
  "name": "我的模板",
  "description": "一句话描述模板风格",
  "props": [
    { "key": "border_padding", "label": "边距比例", "type": "number", "default": 0.05 },
    { "key": "bg_color", "label": "背景颜色", "type": "color", "default": "#FFFFFF" },
    { "key": "show_logo", "label": "显示Logo", "type": "boolean", "default": true }
  ]
}
```

**参数类型说明：**

| type | 说明 | 示例值 |
|------|------|--------|
| `number` | 数值（整数或浮点） | `0.05`、`160` |
| `color` | 颜色（十六进制） | `"#FFFFFF"` |
| `boolean` | 布尔开关 | `true` / `false` |
| `string` | 文本字符串 | `"KODAK 400TX"` |

> `border_padding` 和 `bg_color` 是合成阶段使用的保留参数，建议所有模板都包含。

### 第三步：编写 template.svg

`template.svg` 是一个 Jinja2 + SVG 混合模板。渲染时引擎会注入以下上下文变量：

**`exif` — EXIF 拍摄信息**

| 变量 | 说明 | 示例 |
|------|------|------|
| `exif.make` | 相机厂商 | `"Canon"` |
| `exif.model` | 相机型号 | `"EOS R5"` |
| `exif.lens_model` | 镜头型号 | `"RF 50mm F1.2L USM"` |
| `exif.focal_length` | 焦距 | `"50mm"` |
| `exif.aperture` | 光圈 | `"f/1.8"` |
| `exif.exposure_time` | 快门速度 | `"1/250s"` |
| `exif.iso` | 感光度 | `"ISO 100"` |
| `exif.datetime_original` | 拍摄日期 | `"2024:06:15 14:30:00"` |

**`assets` — Logo 资源**

| 变量 | 说明 |
|------|------|
| `assets.make_logo_base64` | 厂商 Logo 的 Base64 data URI（原色） |
| `assets.make_logo_auto_base64` | 厂商 Logo 的 Base64 data URI（跟随 currentColor） |

**`layout` — 原始图片尺寸**

| 变量 | 说明 |
|------|------|
| `layout.image_width` | 图片宽度（像素） |
| `layout.image_height` | 图片高度（像素） |

**`props` — 用户参数**

即 `config.json` 中定义的参数，用户可通过 CLI `--prop key=value` 覆盖默认值。

### 最小模板示例

以下是一个仅在底部显示相机信息的最小模板：

```svg
{% set img_w = layout.image_width %}
{% set img_h = layout.image_height %}
{% set pad = (img_w * props.border_padding) | int %}
{% set bar_h = 120 %}
{% set canvas_w = img_w + pad * 2 %}
{% set canvas_h = img_h + pad * 2 + bar_h %}

<svg xmlns="http://www.w3.org/2000/svg"
     width="{{ canvas_w }}" height="{{ canvas_h }}"
     viewBox="0 0 {{ canvas_w }} {{ canvas_h }}">

    <rect width="100%" height="100%" fill="transparent" />

    <text x="{{ pad }}" y="{{ pad + img_h + pad + 70 }}"
          font-family="Arial, sans-serif" font-size="40"
          fill="{{ props.font_color | default('#333') }}">
        {{ exif.make }} {{ exif.model }} · {{ exif.focal_length }} {{ exif.aperture }}
    </text>
</svg>
```

### 关键约束

1. **SVG 背景必须透明** — 合成阶段由 Pillow 创建背景画布，SVG 层通过 alpha 通道叠加
2. **画布尺寸必须包含边距** — SVG 的 `width`/`height` 决定最终输出尺寸，需将 `border_padding` 计算在内
3. **resvg 兼容性** — 栅格化引擎为 resvg，支持大部分 SVG 特性（形状、文字、渐变、滤镜如 `feGaussianBlur`），但不支持 CSS 动画、`foreignObject`、JavaScript
4. **字体** — 使用系统字体，建议指定通用 fallback（如 `font-family="Arial, Helvetica, sans-serif"`）
5. **自定义图片位置** — 如果模板需要非对称布局（如胶片模板两侧有额外边栏），在 `config.json` 中添加 `image_offset_x` / `image_offset_y` 参数控制图片在画布中的偏移

### 验证

模板创建后即可直接使用：

```bash
# 确认模板出现在列表
uv run python -m app.cli templates

# 测试渲染
uv run python -m app.cli process photo.jpg -o test_output.jpg -t my_template

# 如果模板在外部目录
uv run python -m app.cli templates --templates-dir /path/to/my_templates
uv run python -m app.cli process photo.jpg -o test_output.jpg -t my_template \
  --templates-dir /path/to/my_templates
```

## 项目结构

```
PhotoSuit/
├── pyproject.toml              # 项目配置与依赖
├── app/
│   ├── cli.py                  # Typer CLI 入口
│   ├── gui.py                  # Tkinter GUI 入口
│   ├── designer.py             # 模板设计器 GUI
│   ├── pipeline.py             # 5 阶段处理管线
│   ├── exif_parser.py          # EXIF 解析器
│   ├── normalizer.py           # 数据归一化（厂商映射、格式化、Logo 加载）
│   ├── renderer.py             # Jinja2 模板渲染引擎
│   ├── rasterizer.py           # SVG → PNG 栅格化（resvg）
│   ├── compositor.py           # 图像合成引擎（Pillow）
│   ├── assets/logos/           # 厂商 Logo SVG
│   └── templates/
│       ├── default_white/      # 默认白框模板
│       │   ├── config.json
│       │   └── template.svg
│       └── film_strip/         # 胶片边框模板
│           ├── config.json
│           └── template.svg
└── tests/
    └── test_pipeline.py
```
