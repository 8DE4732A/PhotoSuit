这是一份基于 Python、以**“SVG + Jinja2 模板引擎”**为核心、支持根据 EXIF 数据动态渲染内容（如相机 Logo）的图像处理工具的技术详细设计文档。

---

# 图像边框与水印合成工具 (Python 版) - 技术详细设计文档

## 1. 概述 (Overview)

本项目旨在开发一款高性能、高扩展性的图像处理工具，核心功能是读取照片的 EXIF 信息，并根据用户选择的模板，动态生成带有相机参数、厂商 Logo 及个性化排版的边框或水印。
系统采用 **Python** 作为核心驱动，核心排版引擎采用 **SVG + Jinja2** 方案，底层图像合成采用 **PyVips / Pillow**，并解耦接口层以支持 CLI、GUI (桌面端) 和 Web 端三种交互形态。

## 2. 系统架构设计 (Architecture)

系统分为三层架构：

* **接入层 (Interface Layer)**：
* **CLI**：基于 `Typer`，提供自动化脚本和批处理能力。
* **Web**：基于 `FastAPI`，提供 RESTful API，接收前端图片上传并返回合成结果。
* **GUI**：基于 `NiceGUI` 或 `PySide6`，提供跨平台的桌面图形界面。


* **适配层 (Adapter Layer)**：
* 参数校验、配置管理、模板元数据加载、任务队列调度。


* **核心引擎层 (Core Engine)**：
* **EXIF 解析器**：快速读取图片头部信息。
* **数据归一化模块**：清洗 EXIF 数据（如统一厂商名称映射到特定 Logo）。
* **模板渲染引擎 (Jinja2)**：将归一化数据注入 SVG 模板。
* **栅格化器 (Rasterizer)**：将生成的 SVG 转换为带透明通道的 PNG 像素层。
* **图像合成引擎**：底层像素级合并，输出最终高清图片。



## 3. 动态模板系统设计 (核心要点)

### 3.1 模板目录结构

模板系统采用“插件式”设计，每个模板是一个独立的文件夹，包含结构声明、资源和默认配置。全局还包含一个公共资产库（如各个相机的 Logo）。

```text
app/
├── assets/
│   └── logos/                 # 全局公共资产：相机厂商Logo (SVG格式)
│       ├── apple.svg
│       ├── canon.svg
│       ├── dji.svg
│       ├── fujifilm.svg
│       └── sony.svg
└── templates/
    ├── default_white/         # 模板A：默认白框
    │   ├── template.svg       # Jinja2 语法的 SVG 模版文件
    │   ├── config.json        # 模板的元数据与可调参数定义
    │   └── preview.jpg        # 模板预览图（供UI展示）
    └── polaroid/              # 模板B：拍立得风格
        ├── template.svg
        └── config.json

```

### 3.2 配置文件规范 (`config.json`)

定义模板的基础信息以及**向外暴露的可调参数**，前端/GUI 依据此文件动态生成设置表单。

```json
{
  "id": "default_white",
  "name": "经典白底相框",
  "description": "底部留白，左侧显示相机Logo，右侧显示参数",
  "props": [
    { "key": "border_padding", "label": "边距比例", "type": "number", "default": 0.05 },
    { "key": "bg_color", "label": "背景颜色", "type": "color", "default": "#FFFFFF" },
    { "key": "show_logo", "label": "显示厂商Logo", "type": "boolean", "default": true }
  ]
}

```

### 3.3 动态内容注入机制 (EXIF -> Logo映射)

为了实现“根据相机型号动态展示图标”，需要设计一个**数据归一化与资源注入流水线**：

1. **原始 EXIF 提取**：提取 `Make` (制造商) 字段，如 `"CANON INC."` 或 `"Apple"`。
2. **名称清洗与映射 (Normalizer)**：
编写一个映射字典或正则匹配器，将杂乱的 EXIF 厂商名映射为标准的文件名标识：
* `"CANON INC."` -> `"canon"`
* `"NIKON CORPORATION"` -> `"nikon"`


3. **Logo 资源 Base64 预处理**：
由于后续的 SVG 渲染器需要严格的自包含上下文，最佳实践是将提取到的标准 Logo（如 `canon.svg`）转换为 `Base64` 编码的数据 URI。
4. **Jinja2 上下文组装**：将处理好的数据作为 Context 传入。

**组装后的 Context 示例：**

```python
context = {
    "exif": {
        "make": "Canon",
        "model": "Canon EOS R5",
        "focal_length": "50mm",
        "aperture": "f/1.8",
        "iso": "100"
    },
    "props": {
        "bg_color": "#FFFFFF",
        "show_logo": True
    },
    "assets": {
        # 动态计算得出的当前相机 Logo 的 Base64 编码
        "make_logo_base64": "data:image/svg+xml;base64,PHN2ZyB..." 
    },
    "layout": {
        "image_width": 4000,
        "image_height": 3000
    }
}

```

### 3.4 SVG 模板编写示例 (`template.svg`)

使用 Jinja2 语法控制 SVG DOM 的生成。

```xml
<svg xmlns="http://www.w3.org/2000/svg" 
     width="{{ layout.image_width * (1 + props.border_padding * 2) }}" 
     height="{{ layout.image_height * (1 + props.border_padding * 2) + 400 }}">
    
    <rect width="100%" height="100%" fill="{{ props.bg_color }}" />

    <g transform="translate(100, {{ layout.image_height * (1 + props.border_padding) + 100 }})">
        
        {% if props.show_logo and assets.make_logo_base64 %}
            <image href="{{ assets.make_logo_base64 }}" x="0" y="0" width="200" height="80" />
        {% else %}
            <text x="0" y="60" font-family="Arial" font-size="60" fill="#333">{{ exif.make }}</text>
        {% endif %}

        <text x="300" y="60" font-family="Arial" font-size="60" fill="#333">
            {{ exif.model }}
        </text>
        
        <text x="{{ layout.image_width - 200 }}" y="60" font-family="Arial" font-size="40" fill="#666" text-anchor="end">
            {{ exif.focal_length }} | {{ exif.aperture }} | ISO {{ exif.iso }}
        </text>
    </g>
</svg>

```

## 4. 核心流程设计 (Workflow)

整个处理链路分为 5 个核心步骤（`Pipeline`）：

1. **解析阶段 (Parse)**：
* 接收输入图片路径和所选模板 ID。
* 使用 `exifread` 快速读取原图 EXIF，无需加载全图像素。


2. **计算阶段 (Calculate)**：
* 获取原图宽高。
* 加载模板 `config.json`，与用户传入的参数合并。
* 执行 EXIF 到 Logo 的归一化映射，生成完整的 Jinja2 Context。


3. **排版渲染阶段 (Render)**：
* Jinja2 读取 `template.svg` 并注入 Context，输出一个填满实际数据的 SVG 字符串。


4. **栅格化阶段 (Rasterize)**：
* 调用 `cairosvg.svg2png(bytestring=rendered_svg)`，将矢量排版瞬间转换为一张与最终尺寸一致的透明底 PNG（相框层）。


5. **合成导出阶段 (Composite)**：
* 使用 `pyvips` 或 `Pillow`。
* 创建一个背景画布，将原始高清图像粘贴到计算好的坐标上。
* 将栅格化后的“相框层”覆盖/叠加到底图之上。
* 附带保留原始 EXIF 数据，输出为高质量 JPEG 或 WebP。



## 5. 关键技术选型 (Tech Stack)

* **基础语言**：Python 3.12+, 使用uv包管理器
* **图像合成引擎**：`pyvips`（首选，内存占用极低，极速处理超高像素） 或 `Pillow`（备选，部署简单）。
* **EXIF 处理**：`exifread`（读取），`piexif`（回写原始 EXIF 数据到生成图中）。
* **模板引擎**：`Jinja2`。
* **矢量转换（栅格化）**：`cairosvg` 或 `resvg-python`（后者基于 Rust，性能和渲染准确度极佳）。
* **CLI 构建**：`Typer`。
* **Web API 构建**：`FastAPI`。
* **GUI 构建**：`NiceGUI`（使用 Web 技术栈写桌面端）或 `PySide6`。

## 6. 扩展性考量 (Extensibility)

* **新参数支持**：开发者只需在模板的 `config.json` 中添加新配置项，并在 `.svg` 中写好对应的 Jinja2 判断逻辑，UI 接口层即可自动生成控件并传递参数。
* **自定义字体**：通过将字体文件以 Base64 格式内嵌到 SVG `<style>` 的 `@font-face` 中，可实现完美的跨平台排版，保证导出的字体不依赖操作系统的本地字体。
* **多图拼接**：由于计算层与合成层解耦，后续可以通过修改 SVG 布局逻辑，配合 `pyvips` 的拼图 API，轻松实现拍立得拼图、九宫格等复杂排版。