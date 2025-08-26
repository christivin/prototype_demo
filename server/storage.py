import os
import shutil
import uuid
from pathlib import Path
from typing import List, Dict, Optional

from .config import STORAGE_DIR, ensure_directories


class FileStorage:
    """
    负责源文件的持久化存储与索引管理：
    - 保存上传文件到指定磁盘目录
    - 维护文件 ID 与文件元数据的对应关系（简易基于文件系统/内存的索引）
    - 提供列出文件与按 ID 获取文件路径的能力
    注：为简化依赖，这里先使用基于磁盘的轻量实现；必要时可替换为数据库。
    """

    def __init__(self) -> None:
        ensure_directories()
        self.base_dir: Path = STORAGE_DIR
        # 简单的内存索引：进程重启会丢失，可替换为 SQLite
        self._id_to_meta: Dict[str, Dict] = {}

    def save_upload(self, filename: str, content: bytes) -> Dict:
        """
        保存上传文件到存储目录，生成文件 ID。
        返回包含 id、原始文件名、保存路径 的元信息。
        """
        file_id: str = uuid.uuid4().hex
        # 以文件 ID 建子目录，防止同名冲突
        target_dir: Path = self.base_dir / file_id
        target_dir.mkdir(parents=True, exist_ok=True)
        # 保留原始扩展名
        ext = Path(filename).suffix
        stored_path: Path = target_dir / f"source{ext}"
        with open(stored_path, "wb") as f:
            f.write(content)
        meta = {
            "id": file_id,
            "filename": filename,
            "stored_path": str(stored_path.resolve()),
            "dir": str(target_dir.resolve()),
            "size": len(content),
        }
        self._id_to_meta[file_id] = meta
        return meta

    def list_files(self) -> List[Dict]:
        """
        返回当前已知的文件元信息列表。
        仅返回内存索引中的条目。
        """
        return list(self._id_to_meta.values())

    def get_file_meta(self, file_id: str) -> Optional[Dict]:
        """按 ID 获取文件元信息。"""
        return self._id_to_meta.get(file_id)

    def get_file_path(self, file_id: str) -> Optional[Path]:
        """按 ID 获取文件物理路径。"""
        meta = self.get_file_meta(file_id)
        if not meta:
            return None
        path = Path(meta["stored_path"])  # type: ignore[index]
        return path if path.exists() else None

