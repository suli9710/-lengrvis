# 本地数据库保护与恢复检查

更新：2026-11-01。适用于 Windows 本机数据目录中的 `lengrvis.db`。

## SQLite 运行时

- 已包含上游 WAL-reset 修复的运行时使用 WAL；正式构建和 frozen/release 启动拒绝未修复版本。
- 开发环境仍使用旧 Python 时，主数据库改用 DELETE 回滚日志，避免进入受影响 WAL 路径。多连接读写并发性能可能降低；不要把此兼容策略用于正式打包。
- 开发数据库原先处于 WAL 且被其他进程占用时，日志模式转换可能失败。先正常关闭使用该数据目录的旧进程再启动；不要手工删除 `-wal` 或 `-shm`。
- `python backend/main.py --sqlite-runtime-info` 只输出运行时信息；未修复版本返回非零退出码。此路径不加载 guardian、配置或业务数据库。
- 构建脚本检查打包 Python，并运行生成的后端查询真实 SQLite。包装门禁再次检查原始与 portable 后端；探测使用临时数据目录、端口 0 和可清理的进程树，并比较执行前后的文件摘要。
- 最终证据在 `dist/sqlite-runtime-evidence/`，随候选 workflow 上传并绑定实际二进制摘要；二进制继续沿用现有 attestation。构建阶段的 `dist/backend-sqlite-runtime.json` 是签名前证据，不能替代签名后的包装检查。

上游修复版本规则来自 [SQLite WAL-reset 说明](https://www.sqlite.org/wal.html#the_wal_reset_bug)。该规则只检查这一已知缺陷；不代表所选版本永远没有其他问题。

v0.1.3 的隔离发布工作区已使用 Python 3.12.13 / SQLite 3.53.1 创建新的 `.venv`，确认解释器可写入工作区并完成锁定依赖安装。原开发工作区和系统默认 Python 保持原状。此记录仅证明构建环境已就绪；最终 frozen 后端的 SQLite 版本、二进制摘要及 portable 包仍需通过构建和包装门禁验证，不代表候选版本已完成发布验收。

## 加密快照

已有数据库需要新 schema migration 时，在首次 schema 写入前用 SQLite Online Backup 创建一致性快照。新空库不创建无意义备份，已升级完成的数据库不重复备份。

备份先在内存中包含已提交的 WAL 页面，再序列化、检查完整性并使用现有 AES-GCM 加密；磁盘上只写密文，密钥沿用安装级高敏感数据密钥，Windows 使用 DPAPI 包装。文件名绑定为认证数据，重命名或篡改会令验证失败。

- 目录：本机数据目录下 `database-backups/`。
- 保留：最近三份；新快照成功写入后才清理旧快照。
- 上限：单份数据库 256 MiB，复制最多 30 秒；超限或加密失败会阻止升级，不会继续无备份迁移。
- 删除：用户确认“删除本机数据”会清除这些快照，失败按现有部分清除机制报告。
- 并发删除：快照发布前复核持久化删除状态，并在短事务内写入密文；其他进程完成过删除时，正在生成的旧内容快照会被丢弃。
- 限制：这不是磁盘损坏后的异地备份；密钥或 Windows 身份丢失可能无法解密。Linux/macOS 的该加密路径仍受现有密钥存储策略限制。

手动创建与验证（`--data-dir` 必须是要操作的本机数据目录）：

```powershell
.venv/Scripts/python.exe scripts/database_snapshot.py --data-dir D:/path/to/lengrvis-data
.venv/Scripts/python.exe scripts/database_snapshot.py --data-dir D:/path/to/lengrvis-data --verify snapshot-YYYYMMDDTHHMMSSffffffZ-ID.db.enc
```

手动工具要求 SQLite 已含 WAL 修复，未修复开发运行时会明确拒绝。自动迁移备份可在已切换为 DELETE 的开发数据库上执行。

恢复检查将密文解密为只读内存数据库，检查 SQLite 完整性并输出指定表计数；不会输出正文、覆盖在线数据库或重新激活旧审批。**它提供可读取快照和恢复演练能力，不是自动灾难恢复或一键回滚。** 激活历史数据库还需要离线审核审计链、密钥、schema 兼容、授权撤销及外部副作用，当前工具有意不提供直接覆盖入口。

## 数据生命周期范围

| 数据 | 当前保护 | 保留与清除边界 |
| --- | --- | --- |
| 任务、聊天、计划、记忆正文 | 主 SQLite 仍可能为明文；不能因备份加密而宣称全库加密 | 受现有应用清除和相应保留策略控制 |
| 截图及高敏感 payload | 复用现有分项加密策略 | 按录制/任务生命周期处理 |
| 数据库快照 | AES-GCM，Windows DPAPI 包装密钥 | 最近三份，隐私清除同步删除 |
| 文件工具备份、工具结果、诊断包 | 维持各自已有策略；不能一概宣称加密 | 隐私清除同步删除，外发诊断仍需复核 |
| 审计链 | 完整性保护；不是内容保密保证 | 隐私清除后保留删除事件及审计边界 |
| 本地日志 | 沿用脱敏策略 | 应用运行期间不自动删除，仍需按 UI 指引手动清理 |

全库/字段加密的后续迁移必须另行处理 FTS、查询、旧 WAL、索引与密钥恢复，本轮没有改变现有正文存储格式。
