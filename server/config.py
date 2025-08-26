import os
from pathlib import Path


# 获取项目根目录路径
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 读取环境变量中的存储目录，默认使用项目根目录下的 data 目录
STORAGE_DIR = Path(os.environ.get("DOTSOCR_STORAGE_DIR", PROJECT_ROOT / "data/storage"))

# 读取环境变量中的结果输出目录，默认使用项目根目录下的 data/results 目录
RESULTS_DIR = Path(os.environ.get("DOTSOCR_RESULTS_DIR", PROJECT_ROOT / "data/results"))

# SQLite 数据库存放路径，默认使用项目根目录下的 data/dotsocr.db
DB_PATH = Path(os.environ.get("DOTSOCR_DB_PATH", PROJECT_ROOT / "data/dotsocr.db"))


def ensure_directories() -> None:
    """
    确保存储与结果目录存在，不存在则创建。
    """
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

