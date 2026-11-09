"""AI 能力层。

与 routers/ai.py 的分工：
- routers/ai.py 负责 HTTP 契约与原有问答接口；
- 本包负责「可被模型调度的能力单元」（tools/），鉴权、数据范围与审计都在服务端完成。
"""
