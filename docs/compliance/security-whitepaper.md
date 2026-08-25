# Lengrvis 安全白皮书（Security Whitepaper）

**版本**：v0.1（草稿）  
**最后更新**：2026-06-22  
**读者**：评估方、采购方、安全研究者  
> 状态口径：fail-closed。本文档描述设计与现状，凡未经第三方验证之处均如实标注，不夸大为"已认证"。

## 1. 概述

Lengrvis 是本机优先的 OS Agent，可读取文件、执行系统诊断、在审批后修改本机状态，并支持移动端配对与远程输入。其安全模型围绕"数据不出本机""危险操作强制审批""全链路可审计"三条主线设计。

## 2. 数据驻留与最小化

- 任务正文、对话、文件索引、向量、审批与审计记录默认存储于本机 SQLite（`<data_dir>/lengrvis.db`）。
- 无默认云端遥测、无账户体系（当前阶段）。
- 录屏/截图证据为 opt-in，默认关闭。

## 3. 凭据与密钥保护

- 密钥保护：Windows 使用 DPAPI；macOS/Linux 使用系统 keyring（如 Keychain/Secret Service）保存实际 secret，文件中只保留查找句柄。系统密钥库不可用时默认 fail-closed；明文仅允许显式开发/测试豁免。
- 本地数据库文件权限限定为当前用户（Unix 0600 / Windows ACL）。

## 4. 网络边界

- 桌面端 API 绑定环回地址（127.0.0.1 / localhost），不接受外部网络请求。
- 移动远控在 LAN 内进行，非 loopback 通道要求 WSS；配对码为一次性 16 位十六进制令牌。

## 5. 审批与权限模型

- 操作分级（R1 只读 / R2-R3 修改类 / R4 禁区）。R2/R3 修改类操作须用户显式审批；R4（密码、cookie、token、凭据外发，支付/下单类）一律拒绝。
- 审批记录与 `approval_id` 防伪造为高优先级攻击面（见 `SECURITY.md`）。

## 6. 可审计性

- 审计链 `audit_events` 采用 HMAC 哈希链检测记录是否被修改。该机制是 tamper-evident：同一 Windows 用户权限下的攻击者仍可能取得本机密钥材料并重算记录，因此不构成绝对不可篡改或外部不可抵赖证明。
- 隐私擦除等敏感动作追加留痕事件（如 `privacy.local_data_erased`）。

## 7. 脱敏

- 日志写入前经脱敏中间件；诊断包导出时执行路径标签化与敏感字段移除，默认 `public_safe=false`。

## 8. 依赖与供应链安全

- 依赖审计：`npm run audit:deps`（workspace QA、desktop、mobile 的 `npm audit` high+ 失败；根目录 workspace QA 依赖属于 QA/交付工具链清单，不代表产品运行时依赖；backend runtime/build 与 acceleration Python lock 由 `pip-audit` 在当前平台环境 markers 下审计，任意 finding/error fail-closed；跨平台-only marker 依赖需由对应平台或额外 OSV/多平台扫描补证据）。
- 密钥扫描：本地 `npm run security:secrets` / pre-commit 均调用 `scripts/secret_scan.ps1`，使用严格 `.gitleaks-ci.toml`，扫描 Git source snapshot 并显式绕过 `.gitleaksignore` 行号指纹；CI 在 `.github/workflows/security-audit.yml` 中对 tracked source 和 git history 运行 gitleaks。
- SAST：`.github/workflows/codeql.yml` 对 Python 与 TypeScript 运行 CodeQL。
- 安全回归门禁：`npm run qa:gate`（含黄金任务回归 `npm run golden:gate`）、发布前 `npm run release:safety`。

## 9. 已知边界与未完成项

- **第三方渗透测试未做**（含远程输入通道与审批绕过专项 fuzz），见 `PRODUCTIZATION_ISSUES.md`。
- 第三方渗透测试、fuzz 与候选版本人工证据审阅仍在推进。
- 本白皮书不代表已获得任何安全认证；认证进度见 `docs/compliance/certification-roadmap.md`。

## 10. 漏洞报告

见 `SECURITY.md` 与 `.well-known/security.txt`。请遵循协同披露，勿在公开 issue 暴露细节或 PoC。

---

> **免责声明**：本白皮书由 AI 基于代码审计与架构文档生成，描述设计意图与现状。对外提供给采购/评估方前，建议结合实际部署与第三方测试结果复核。
