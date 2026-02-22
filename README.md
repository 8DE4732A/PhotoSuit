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

### 运行测试

```bash
uv run pytest tests/ -v
```

## 项目结构

```
PhotoSuit/
├── pyproject.toml              # 项目配置与依赖
├── app/
│   ├── cli.py                  # Typer CLI 入口
│   ├── pipeline.py             # 5 阶段处理管线
│   ├── exif_parser.py          # EXIF 解析器
│   ├── normalizer.py           # 数据归一化（厂商映射、格式化、Logo 加载）
│   ├── renderer.py             # Jinja2 模板渲染引擎
│   ├── rasterizer.py           # SVG → PNG 栅格化（resvg）
│   ├── compositor.py           # 图像合成引擎（Pillow）
│   ├── assets/logos/           # 厂商 Logo SVG
│   └── templates/
│       └── default_white/      # 默认白框模板
│           ├── config.json
│           └── template.svg
└── tests/
    └── test_pipeline.py
```
