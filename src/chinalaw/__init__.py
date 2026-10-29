"""chinalaw — 中国法律法规检索 CLI。

为 AI agent 查阅中国法律法规而建的命令行工具。
"""

__version__ = "0.7.5"
PROJECT_URL = "https://github.com/nh59yytyd5-dev/chinalaw-cli"
# 公开法规快照的下载与用法页；快照随数据发布更新，这里不写死版本号。
PUBLIC_DATA_URL = "https://law.newcombmath.com/about/data.html"
USER_AGENT_TOKEN = f"chinalaw-cli/{__version__} (+{PROJECT_URL})"

__all__ = ["PROJECT_URL", "PUBLIC_DATA_URL", "USER_AGENT_TOKEN", "__version__"]
