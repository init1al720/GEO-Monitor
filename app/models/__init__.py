"""ORM 模型层（目前未接数据库）。

接入数据库时建议：
1. 在 app/core/db.py 建异步 engine 与 session 工厂；
2. 每个业务域一个文件，如 app/models/user.py，继承 Base；
3. 用 Alembic 管理迁移，迁移脚本放 migrations/。
"""
