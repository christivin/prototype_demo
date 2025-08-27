##  Web 服务

本项目提供“上传文档 → 异步解析 → 状态查询 → 结果下载”的一站式 Web 服务，基于 FastAPI 实现。解析引擎默认使用 `dots_ocr.parser.DotsOCRParser`，并支持 mock 联调模式（无需实际调用模型即可跑通全流程）。

### 系统概述
- **文件存储**：`server/storage.py` 负责将上传的源文件落盘，生成 `file_id`，支持列表与下载。
- **任务管理**：`server/tasks.py` 负责创建解析任务、异步执行、状态跟踪与结果目录管理。
- **配置管理**：`server/config.py` 统一数据路径（存储与结果目录）；可通过环境变量覆盖：
  - `DOTSOCR_STORAGE_DIR` 默认：`<project>/data/storage`
  - `DOTSOCR_RESULTS_DIR` 默认：`<project>/data/results`
  - `DOTSOCR_DB_PATH` 默认：`<project>/data/dotsocr.db`（预留，当前未使用）
- **解析引擎**：`dots_ocr.parser.DotsOCRParser` 支持图片/PDF 的版面与文本解析；可选 fitz 预处理。
- **Web 服务**：`api_service.py` 对外暴露 REST 接口；保持同步解析端点，同时新增文件与任务管理端点。

### 用户动线（从上传到下载）
1. 上传文件，获取 `file_id`
2. 使用 `file_id` 创建解析任务（异步），获取 `task_id`
3. 轮询查询任务状态，直至 `success/failed`
4. 成功后下载任务结果 zip 包（包含布局 JSON、标注图、Markdown 与 jsonl 汇总）

### 启动服务
```bash
python /Users/christivinxu/Desktop/mybox/contract-ocr/api_service.py
# 打开 http://localhost:8001/docs 交互调试
```

### 接口文档

#### 文件管理
- POST `/files/upload` 上传并保存源文件
  - 表单：`file=@/path/to/file.pdf|.jpg|.png`
  - 返回：`{ id, filename, size }`
- GET `/files` 查看所有已上传文件
  - 返回：`[{ id, filename, stored_path, size }]`
- GET `/files/{file_id}` 下载源文件

示例：
```bash
# 上传
curl -F "file=@/path/doc.pdf" http://localhost:8001/files/upload

# 列表
curl http://localhost:8001/files

# 下载
curl -L -o source.pdf http://localhost:8001/files/<file_id>
```

#### 任务管理（异步解析）
- POST `/tasks/parse/{file_id}` 创建解析任务（异步）
  - 查询参数：
    - `prompt_mode` 默认 `prompt_layout_all_en`
    - `fitz_preprocess` 默认 `false`
    - `mock` 默认 `false`；设为 `true` 不调用模型，直接生成模拟结果
  - 返回：`{ task_id }`
- GET `/tasks/{task_id}` 获取任务状态
  - 返回：`{ id, status(pending|running|success|failed), progress(0-100), error? }`
- GET `/tasks` 获取所有任务列表
- GET `/tasks/{task_id}/download` 下载任务结果 zip 包

示例：
```bash
# 创建任务（mock 联调）
curl -X POST "http://localhost:8001/tasks/parse/<file_id>?prompt_mode=prompt_layout_all_en&mock=true"

# 查询状态
curl http://localhost:8001/tasks/<task_id>

# 列表
curl http://localhost:8001/tasks

# 下载结果
curl -L -o result.zip http://localhost:8001/tasks/<task_id>/download
```

#### 同步解析（保留，兼容旧流程）
- POST `/parse/file` | `/parse/image` | `/parse/pdf`：上传文件并同步返回解析结果。

### Mock 测试
创建任务时传 `mock=true`，系统将在任务目录生成最小可用的：
- `filename.json`：伪造布局元素数组
- `filename.md`：示例 Markdown
- `filename.jpg`：占位图（字节 “mock”）
- `filename.jsonl`：结果汇总（便于后续处理）

该模式无需模型或 vLLM 服务即可联调前后端流程和下载逻辑。

### 进阶配置
- 如需使用真实模型推理：
  - vLLM 模式：启动 OpenAI 兼容的 vLLM 服务（默认 `http://localhost:8000/v1`）。
  - HuggingFace 本地模式：下载权重到 `weights/DotsOCR` 后启用 `use_hf=True`（参考 `dots_ocr/parser.py`）。

### 目录导航
- `api_service.py`：FastAPI 入口
- `server/config.py`：目录与配置
- `server/storage.py`：文件存储
- `server/tasks.py`：异步任务
- `dots_ocr/parser.py`：解析引擎

