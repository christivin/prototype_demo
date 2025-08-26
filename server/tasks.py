import json
import os
import threading
import uuid
from pathlib import Path
from typing import Dict, Optional, List, Callable

from .config import RESULTS_DIR, ensure_directories


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TaskManager:
    """
    负责解析任务的异步调度与状态管理：
    - 创建任务并返回任务 ID
    - 后台线程执行实际解析回调
    - 跟踪任务进度与结果输出目录
    注：为避免额外依赖，这里使用线程作为简易队列。
    """

    def __init__(self) -> None:
        ensure_directories()
        self.base_dir: Path = RESULTS_DIR
        self._tasks: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def _update(self, task_id: str, **kwargs) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(kwargs)

    def create_task(self, job: Callable[[str], Dict]) -> Dict:
        """
        创建任务并异步执行 job 回调。
        job 接收任务输出目录路径，返回 {"ok": bool, "error": str|None, "artifacts": dict}
        """
        task_id = uuid.uuid4().hex
        task_dir = self.base_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "id": task_id,
            "status": TaskStatus.PENDING,
            "progress": 0,
            "dir": str(task_dir.resolve()),
            "error": None,
            "artifacts": {},
        }
        self._tasks[task_id] = record

        def _run():
            try:
                self._update(task_id, status=TaskStatus.RUNNING, progress=5)
                result = job(str(task_dir.resolve()))
                if result.get("ok"):
                    self._update(task_id, status=TaskStatus.SUCCESS, progress=100, artifacts=result.get("artifacts", {}))
                else:
                    self._update(task_id, status=TaskStatus.FAILED, progress=100, error=result.get("error", "unknown error"))
            except Exception as e:
                self._update(task_id, status=TaskStatus.FAILED, progress=100, error=str(e))

        threading.Thread(target=_run, daemon=True).start()
        return record

    def get(self, task_id: str) -> Optional[Dict]:
        return self._tasks.get(task_id)

    def list(self) -> List[Dict]:
        return list(self._tasks.values())

