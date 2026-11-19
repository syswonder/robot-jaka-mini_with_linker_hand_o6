# Description: 网页标定台的公共层（从 robot-agilex-robonix 移植，只服务于本部署）。
#
# 与旧 CLI 工具（calibrate_handeye_2d.py）并存：旧工具走 cv2 窗口镜像（:8079），
# 这里是一套独立的 Flask 页面（默认 :8080），按钮化内参/手眼/验证三步。
