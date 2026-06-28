# 安全政策（Security Policy）

Lengrvis 是一个本机优先的 OS Agent：它可以读取文件、执行系统诊断、在审批后修改本机状态，并支持移动端配对与远程输入。这类能力意味着安全报告会被高优先级对待。

## 支持范围（Supported Versions）

| 版本 | 是否接收安全修复 |
| --- | --- |
| `main` 分支最新提交 | 是 |
| 历史 tag / 旧构建 | 否（请升级到最新版本后复现） |

当前产品处于发布前阶段（无公开分发渠道），安全修复只面向 `main`。

## 如何报告漏洞（Reporting a Vulnerability）

- **请勿**在公开 issue / 讨论区直接披露漏洞细节或 PoC。
- 报告渠道（按优先顺序）：
  1. 仓库托管平台的私密安全通告（GitHub Security Advisories → Report a vulnerability）。
  2. 项目维护者的私密联系渠道（见仓库主页 owner 资料）。
- 报告请尽量包含：受影响的入口（API / WebSocket / 桌面 IPC / 移动配对）、复现步骤、影响评估、建议修复方向。涉及隐私数据的截图请先脱敏。

## 响应 SLA

| 阶段 | 目标时限 |
| --- | --- |
| 首次响应（确认收到） | 3 个工作日内 |
| 初步定级（严重性 / 影响面） | 7 个工作日内 |
| 高危（可远程触发的越权执行、审批绕过、凭据泄露） | 14 天内给出修复或缓解 |
| 中低危 | 30-90 天内随版本修复 |

在修复发布前，请遵循协同披露（coordinated disclosure），不公开细节。

## 高优先级攻击面（What We Care Most About）

以下问题会被按最高严重性处理：

- **审批绕过**：在没有用户审批的情况下执行 R2/R3 修改类操作，或伪造 `approved` / `approval_id`。
- **R4 禁区**：诱导 Agent 读取/外发密码、cookie、token、凭据，或执行支付/下单类操作。
- **远程输入通道**：移动端配对、远程屏幕/输入 WebSocket 的鉴权、scope、token 泄露与重放。
- **路径越权**：逃逸授权目录（路径穿越、符号链接、NTFS 数据流）。
- **脱敏失效**：诊断包 / 时间线 / 公开 API 泄露本机路径、密钥、任务正文、设备标识。

## 自查与依赖审计

- 依赖漏洞扫描入口：`npm run audit:deps`（desktop/mobile `npm audit` + Python lock `pip-audit`；Python 审计按当前平台 environment markers 解析，finding/error 即失败）。
- 安全相关回归：`npm run qa:gate`（含黄金任务回归 `npm run golden:gate`：R3 删除必须停在审批、R4 禁区必须拒绝、越权路径必须抛 `SecurityError`）。
- 发布前安全检查：`npm run release:safety`。

## 边界说明

本文件描述的是漏洞报告与响应流程；它不代表产品已经过外部渗透测试。第三方安全审计（含远程输入通道与审批绕过的专项 fuzz）仍是发布前的未完成项，见 `PRODUCTIZATION_ISSUES.md`。
