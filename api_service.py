"""
DotsOCR API Service
==================

基于FastAPI的OCR文档解析API服务，支持图像和PDF文件的文本识别和布局分析。

功能特点:
- 支持图像格式: JPG, JPEG, PNG
- 支持PDF文档解析
- 多种提示模式选择
- 返回结构化的布局信息
- 自动临时文件管理

Author: Grant
Version: 1.0.0
"""

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
from pathlib import Path
import tempfile
import uuid
import json
import shutil
from typing import Optional, List, Dict, Any

# DotsOCR核心模块导入
from dots_ocr.parser import DotsOCRParser
from dots_ocr.utils.consts import MIN_PIXELS, MAX_PIXELS
from server.storage import FileStorage
from server.tasks import TaskManager, TaskStatus
from server.config import ensure_directories

# ==================== FastAPI应用初始化 ====================

app = FastAPI(
    title="DotsOCR API Service",
    description="高性能OCR文档解析API服务，支持PDF和图像文件的文本识别与布局分析",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# ==================== 全局配置 ====================

# 初始化通用目录
ensure_directories()

# DotsOCR解析器实例 - 配置默认参数
dots_parser = DotsOCRParser(
    ip="localhost",          # VLLM服务器IP地址
    port=8000,              # VLLM服务器端口
    dpi=200,                # 图像DPI设置
    min_pixels=MIN_PIXELS,  # 最小像素限制
    max_pixels=MAX_PIXELS   # 最大像素限制
)

# 文件存储与任务管理实例
file_storage = FileStorage()
task_manager = TaskManager()

# ==================== 数据模型定义 ====================

class ParseRequest(BaseModel):
    """解析请求参数模型"""
    prompt_mode: str = "prompt_layout_all_en"  # 提示模式
    fitz_preprocess: bool = False              # 是否启用fitz预处理

class ParseResult(BaseModel):
    """解析结果模型"""
    success: bool                              # 解析是否成功
    total_pages: int                          # 总页数
    results: List[Dict[str, Any]]             # 解析结果列表

class UploadResponse(BaseModel):
    """上传文件响应模型"""
    id: str
    filename: str
    size: int

class FileMeta(BaseModel):
    """文件元信息模型"""
    id: str
    filename: str
    stored_path: str
    size: int

class TaskCreateResponse(BaseModel):
    """任务创建响应模型"""
    task_id: str

class TaskInfo(BaseModel):
    """任务信息"""
    id: str
    status: str
    progress: int
    error: Optional[str] = None

# ==================== 工具函数 ====================

def create_temp_session_dir() -> tuple[str, str]:
    """
    创建唯一的临时会话目录
    
    Returns:
        tuple: (临时目录路径, 会话ID)
    """
    session_id = uuid.uuid4().hex[:8]
    temp_dir = os.path.join(tempfile.gettempdir(), f"dots_ocr_api_{session_id}")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir, session_id

def validate_file_upload(file: UploadFile, allowed_extensions: List[str]) -> str:
    """
    验证上传文件的有效性
    
    Args:
        file: 上传的文件对象
        allowed_extensions: 允许的文件扩展名列表
        
    Returns:
        str: 文件扩展名
        
    Raises:
        HTTPException: 文件验证失败时抛出异常
    """
    # 检查文件是否存在
    if not file:
        raise HTTPException(status_code=400, detail="未上传文件")
    
    # 检查文件名是否存在
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名缺失")
    
    try:
        # 提取文件扩展名
        file_ext = Path(file.filename).suffix.lower()
    except TypeError:
        raise HTTPException(status_code=400, detail="文件名格式无效")
    
    # 验证文件格式
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的文件格式。支持的格式: {', '.join(allowed_extensions)}"
        )
    
    return file_ext

async def save_upload_to_temp(file: UploadFile, temp_dir: str, session_id: str, file_ext: str) -> str:
    """
    将上传的文件保存到临时目录
    
    Args:
        file: 上传的文件对象
        temp_dir: 临时目录路径
        session_id: 会话ID
        file_ext: 文件扩展名
        
    Returns:
        str: 保存的文件绝对路径
        
    Raises:
        HTTPException: 文件保存失败时抛出异常
    """
    # 读取文件内容
    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="上传的文件为空")
    
    # 生成临时文件路径
    temp_path = os.path.join(temp_dir, f"upload_{session_id}{file_ext}")
    
    try:
        # 写入文件内容到临时文件
        with open(temp_path, "wb") as buffer:
            buffer.write(file_content)
        
        # 验证文件是否成功创建
        abs_temp_path = os.path.abspath(temp_path)
        if not os.path.exists(abs_temp_path):
            raise HTTPException(status_code=500, detail="临时文件创建失败")
            
        print(f"DEBUG: 文件已保存到: {abs_temp_path} ({len(file_content)} bytes)")
        return abs_temp_path
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存错误: {str(e)}")

def cleanup_temp_directory(temp_dir: str):
    """
    清理临时目录及其所有内容
    
    Args:
        temp_dir: 要清理的临时目录路径
    """
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"DEBUG: 已清理临时目录: {temp_dir}")
    except Exception as e:
        print(f"WARNING: 清理临时目录失败: {str(e)}")

def load_layout_info(layout_info_path: str) -> Dict[str, Any]:
    """
    加载布局信息文件
    
    Args:
        layout_info_path: 布局信息文件路径
        
    Returns:
        Dict: 布局信息数据，加载失败时返回空字典
    """
    if not layout_info_path or not os.path.exists(layout_info_path):
        return {}
    
    try:
        with open(layout_info_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"WARNING: 布局信息文件读取失败: {str(e)}")
        return {}

# ==================== API端点定义 ====================

# -------- 文件管理：上传/列表/下载 --------

@app.post("/files/upload", response_model=UploadResponse, summary="上传并保存源文件")
async def upload_file(file: UploadFile = File(..., description="要上传的文件 (PDF, JPG, JPEG, PNG)")):
    """
    上传源文件并保存至磁盘，返回文件 ID。
    """
    # 验证扩展名
    _ = validate_file_upload(file, ['.pdf', '.jpg', '.jpeg', '.png'])
    # 读取内容
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")
    # 存储并返回 ID
    meta = file_storage.save_upload(file.filename or "upload", content)
    return UploadResponse(id=meta["id"], filename=meta["filename"], size=meta["size"])  # type: ignore[index]


@app.get("/files", response_model=List[FileMeta], summary="查看所有已上传文件")
async def list_files():
    """返回文件元信息列表。"""
    items = file_storage.list_files()
    return [FileMeta(id=i["id"], filename=i["filename"], stored_path=i["stored_path"], size=i["size"]) for i in items]  # type: ignore[index]


@app.get("/files/{file_id}", summary="按ID下载源文件")
async def download_file(file_id: str):
    """根据文件 ID 下载源文件。"""
    path = file_storage.get_file_path(file_id)
    if not path:
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=str(path), filename=Path(path).name)


# -------- 任务管理：创建/状态/列表/下载结果 --------

@app.post("/tasks/parse/{file_id}", response_model=TaskCreateResponse, summary="创建解析任务(异步)")
async def create_parse_task(
    file_id: str,
    prompt_mode: str = "prompt_layout_all_en",
    fitz_preprocess: bool = False,
    mock: bool = False,
):
    """根据文件ID创建解析任务，后台异步执行。"""
    source_path = file_storage.get_file_path(file_id)
    if not source_path:
        raise HTTPException(status_code=404, detail="文件不存在")

    def _job(task_output_dir: str):
        # 任务执行函数：调用 DotsOCRParser 进行解析，并将成果文件写入 task_output_dir
        try:
            # 依据 mock 参数决定是否真实调用模型
            ext = Path(source_path).suffix.lower()
            filename = Path(source_path).stem
            results = []
            if mock:
                # 写入模拟结果文件，避免实际模型调用
                os.makedirs(task_output_dir, exist_ok=True)
                layout_json_path = Path(task_output_dir) / f"{filename}.json"
                layout_img_path = Path(task_output_dir) / f"{filename}.jpg"
                md_path = Path(task_output_dir) / f"{filename}.md"
                # 伪造一个最小可用布局
                fake_cells = [{
                    "bbox": [10, 10, 200, 60],
                    "category": "Title",
                    "text": "Mock Title"
                }, {
                    "bbox": [10, 80, 300, 140],
                    "category": "Text",
                    "text": "This is a mock paragraph for testing."
                }]
                with open(layout_json_path, 'w', encoding='utf-8') as f:
                    json.dump(fake_cells, f, ensure_ascii=False)
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write("# Mock Result\n\nThis is a mock markdown output.")
                # 占位图像
                with open(layout_img_path, 'wb') as f:
                    f.write(b"mock")
                # 写 jsonl 汇总
                with open(Path(task_output_dir) / f"{filename}.jsonl", 'w', encoding='utf-8') as w:
                    w.write(json.dumps({
                        "page_no": 0,
                        "layout_info_path": str(layout_json_path),
                        "layout_image_path": str(layout_img_path),
                        "md_content_path": str(md_path),
                        "file_path": str(source_path),
                    }, ensure_ascii=False) + "\n")
            else:
                # 将解析输出定向到任务目录
                if ext == '.pdf':
                    results = dots_parser.parse_pdf(
                        input_path=str(source_path),
                        filename=filename,
                        prompt_mode=prompt_mode,
                        save_dir=task_output_dir,
                    )
                else:
                    results = dots_parser.parse_image(
                        input_path=str(source_path),
                        filename=filename,
                        prompt_mode=prompt_mode,
                        save_dir=task_output_dir,
                        fitz_preprocess=fitz_preprocess,
                    )
            # 汇总 artifacts：返回主要文件路径，方便客户端下载
            artifacts = {
                "result_jsonl": str(Path(task_output_dir) / f"{filename}.jsonl"),
                "dir": task_output_dir,
            }
            return {"ok": True, "artifacts": artifacts}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    task = task_manager.create_task(_job)
    return TaskCreateResponse(task_id=task["id"])  # type: ignore[index]


@app.get("/tasks/{task_id}", response_model=TaskInfo, summary="查看任务状态")
async def get_task(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskInfo(id=task["id"], status=task["status"], progress=task.get("progress", 0), error=task.get("error"))  # type: ignore[index]


@app.get("/tasks", response_model=List[TaskInfo], summary="查看所有任务")
async def list_tasks():
    tasks = task_manager.list()
    return [TaskInfo(id=t["id"], status=t["status"], progress=t.get("progress", 0), error=t.get("error")) for t in tasks]  # type: ignore[index]


@app.get("/tasks/{task_id}/download", summary="下载任务结果目录(压缩包)")
async def download_task_result(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task_dir = Path(task["dir"])  # type: ignore[index]
    if not task_dir.exists():
        raise HTTPException(status_code=404, detail="任务输出目录不存在")
    # 将任务目录打包为 zip
    zip_path = task_dir.with_suffix("")
    archive_file = str(zip_path)  # shutil.make_archive 会自动加后缀
    archive = shutil.make_archive(base_name=archive_file, format="zip", root_dir=str(task_dir))
    return FileResponse(path=archive, filename=f"{task_id}.zip")

@app.post("/parse/image", response_model=ParseResult, summary="解析图像文件")
async def parse_image(
    file: UploadFile = File(..., description="要解析的图像文件 (JPG, JPEG, PNG)"),
    prompt_mode: str = "prompt_layout_all_en",
    fitz_preprocess: bool = False
):
    """
    解析图像文件并提取文本和布局信息
    
    Args:
        file: 上传的图像文件
        prompt_mode: 提示模式 (prompt_layout_all_en, prompt_layout_only_en, prompt_ocr)
        fitz_preprocess: 是否启用fitz预处理（推荐用于低DPI图像）
        
    Returns:
        ParseResult: 包含解析结果的响应对象
        
    Raises:
        HTTPException: 当文件验证、处理或解析失败时
    """
    temp_dir = None
    
    try:
        # 1. 验证文件格式
        file_ext = validate_file_upload(file, ['.jpg', '.jpeg', '.png'])
        
        # 2. 创建临时会话目录
        temp_dir, session_id = create_temp_session_dir()
        print(f"DEBUG: 创建会话 {session_id}, 临时目录: {temp_dir}")
        
        # 3. 保存上传文件
        temp_file_path = await save_upload_to_temp(file, temp_dir, session_id, file_ext)
        
        # 4. 创建输出目录
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        # 5. 调用DotsOCR解析器处理图像
        print(f"DEBUG: 开始解析图像，模式: {prompt_mode}")
        results = dots_parser.parse_image(
            input_path=temp_file_path,
            filename=f"api_image_{session_id}",
            prompt_mode=prompt_mode,
            save_dir=output_dir,
            fitz_preprocess=fitz_preprocess
        )
        
        if not results:
            raise HTTPException(status_code=500, detail="解析器未返回结果")
        
        # 6. 处理解析结果
        result = results[0]  # 图像解析只返回一个结果
        layout_info = load_layout_info(result.get('layout_info_path'))
        
        print(f"DEBUG: 图像解析完成，检测到 {len(layout_info)} 个元素")
        
        # 7. 构造响应
        return ParseResult(
            success=True,
            total_pages=1,
            results=[{
                "page_no": 0,
                "full_layout_info": layout_info,
                "session_id": session_id,
                "filtered": result.get('filtered', False)
            }]
        )
        
    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        print(f"ERROR: 图像解析异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"解析过程发生错误: {str(e)}")
    finally:
        # 8. 清理临时目录
        if temp_dir:
            cleanup_temp_directory(temp_dir)

@app.post("/parse/pdf", response_model=ParseResult, summary="解析PDF文件")
async def parse_pdf(
    file: UploadFile = File(..., description="要解析的PDF文件"),
    prompt_mode: str = "prompt_layout_all_en",
    fitz_preprocess: bool = False
):
    """
    解析PDF文件并提取每页的文本和布局信息
    
    Args:
        file: 上传的PDF文件
        prompt_mode: 提示模式 (prompt_layout_all_en, prompt_layout_only_en, prompt_ocr)
        fitz_preprocess: fitz预处理参数（对PDF文件通常不需要）
        
    Returns:
        ParseResult: 包含所有页面解析结果的响应对象
        
    Raises:
        HTTPException: 当文件验证、处理或解析失败时
    """
    temp_dir = None
    
    try:
        # 1. 验证PDF文件格式
        file_ext = validate_file_upload(file, ['.pdf'])
        
        # 2. 创建临时会话目录
        temp_dir, session_id = create_temp_session_dir()
        print(f"DEBUG: 创建PDF解析会话 {session_id}")
        
        # 3. 保存上传的PDF文件
        temp_file_path = await save_upload_to_temp(file, temp_dir, session_id, file_ext)
        
        # 4. 创建输出目录
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        
        # 5. 调用DotsOCR解析器处理PDF
        print(f"DEBUG: 开始解析PDF，模式: {prompt_mode}")
        results = dots_parser.parse_pdf(
            input_path=temp_file_path,
            filename=f"api_pdf_{session_id}",
            prompt_mode=prompt_mode,
            save_dir=output_dir
        )
        
        if not results:
            raise HTTPException(status_code=500, detail="PDF解析器未返回结果")
        
        # 6. 处理多页解析结果
        formatted_results = []
        for result in results:
            layout_info = load_layout_info(result.get('layout_info_path'))
            
            formatted_results.append({
                "page_no": result.get('page_no', 0),
                "full_layout_info": layout_info,
                "session_id": session_id,
                "filtered": result.get('filtered', False)
            })
        
        total_elements = sum(len(res["full_layout_info"]) for res in formatted_results)
        print(f"DEBUG: PDF解析完成，共 {len(results)} 页，检测到 {total_elements} 个元素")
        
        # 7. 构造响应
        return ParseResult(
            success=True,
            total_pages=len(results),
            results=formatted_results
        )
        
    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        print(f"ERROR: PDF解析异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"PDF解析过程发生错误: {str(e)}")
    finally:
        # 8. 清理临时目录
        if temp_dir:
            cleanup_temp_directory(temp_dir)

@app.post("/parse/file", response_model=ParseResult, summary="通用文件解析接口")
async def parse_file(
    file: UploadFile = File(..., description="要解析的文件 (支持PDF, JPG, JPEG, PNG)"),
    prompt_mode: str = "prompt_layout_all_en",
    fitz_preprocess: bool = False
):
    """
    通用文件解析接口，自动识别文件类型并调用相应的解析方法
    
    Args:
        file: 上传的文件（PDF或图像）
        prompt_mode: 提示模式
        fitz_preprocess: 是否启用fitz预处理
        
    Returns:
        ParseResult: 解析结果，格式根据文件类型自动适配
        
    Raises:
        HTTPException: 当文件类型不支持或解析失败时
    """
    try:
        # 1. 验证文件并确定类型
        file_ext = validate_file_upload(file, ['.pdf', '.jpg', '.jpeg', '.png'])
        
        # 2. 根据文件类型路由到相应的处理函数
        if file_ext == '.pdf':
            print(f"DEBUG: 检测到PDF文件，路由到PDF解析")
            return await parse_pdf(file, prompt_mode, fitz_preprocess)
        elif file_ext in ['.jpg', '.jpeg', '.png']:
            print(f"DEBUG: 检测到图像文件 {file_ext}，路由到图像解析")
            return await parse_image(file, prompt_mode, fitz_preprocess)
        else:
            # 这种情况理论上不会发生，因为validate_file_upload会先检查
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file_ext}")
            
    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        print(f"ERROR: 通用文件解析异常: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文件解析过程发生错误: {str(e)}")

# ==================== 健康检查和信息端点 ====================

@app.get("/health", summary="健康检查")
async def health_check():
    """
    API服务健康检查端点
    
    Returns:
        dict: 服务状态信息
    """
    return {
        "status": "healthy",
        "service": "DotsOCR API",
        "version": "1.0.0",
        "parser_config": {
            "ip": dots_parser.ip,
            "port": dots_parser.port,
            "min_pixels": dots_parser.min_pixels,
            "max_pixels": dots_parser.max_pixels
        }
    }

@app.get("/", summary="API信息")
async def root():
    """
    API根端点，返回服务基本信息
    
    Returns:
        dict: API基本信息
    """
    return {
        "message": "DotsOCR API Service",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }

# ==================== 应用启动 ====================

if __name__ == "__main__":
    import uvicorn
    
    print("=" * 60)
    print("🚀 启动DotsOCR API服务")
    print("=" * 60)
    print(f"📍 服务地址: http://0.0.0.0:8001")
    print(f"📚 API文档: http://0.0.0.0:8001/docs")
    print(f"🔧 配置信息: VLLM服务器 {dots_parser.ip}:{dots_parser.port}")
    print("=" * 60)
    
    # 启动uvicorn服务器
    uvicorn.run(
        app, 
        host="0.0.0.0",     # 监听所有网络接口
        port=8001,          # 服务端口
        reload=False,       # 生产环境建议关闭自动重载
        workers=1           # 工作进程数
    )