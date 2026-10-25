"""测试包。运行：python -m unittest discover -s tests -t . -v"""

import logging

# 测试期间静默业务日志，避免 Windows 控制台编码噪音
logging.getLogger("qqwarframe").addHandler(logging.NullHandler())
logging.getLogger("qqwarframe").propagate = False
