# AI-ERP 智能进销存 / ERP 系统

面向中小企业的本地化智能 ERP 系统，覆盖采购、销售、库存、财务、审批、报表全流程，内置 AI 智能助手与桌面端，开箱即用、数据本地存储。

## 功能特性

- **进销存核心**：采购、销售、报价、入库、出库、库存、盘点、调拨、退货
- **财务一体化**：应收应付、收付款、银行对账、台账、税务报表、期末结账
- **流程审批**：多级审批、审批规则与 SLA、三单匹配（采购—入库—发票）
- **生产与 BOM**：物料清单、生产管理、订单看板
- **AI 智能助手**：自然语言查询、入库单照片识别、智能问答（可选接入大模型）
- **风控与自动化**：客户信用管控、风险雷达、自动化规则、定时调度预警
- **协同与审计**：飞书群机器人通知、站内收件箱、审计日志、单据打印
- **数据交换**：Excel/CSV 导入导出、数据备份与恢复

## 技术栈

- 后端：FastAPI + SQLAlchemy 2 + SQLite（可切换 MySQL / PostgreSQL）
- 前端：Vue 3 + Vite + Element Plus + ECharts + Pinia
- 桌面端：Electron + electron-builder（Windows NSIS 安装包）

## 目录结构

| 目录 | 说明 |
|------|------|
| `backend/` | FastAPI 后端服务，含 AI 助手、审批、财务等模块 |
| `frontend/` | Vue 3 前端应用 |
| `desktop/` | Electron 桌面端与打包脚本 |

## 快速开始

### 方式一：直接使用桌面版（推荐）

下载最新 Windows 安装包，双击安装后以本机模式运行：

https://github.com/hukun2005/ai-erp/releases/latest

### 方式二：从源码运行

后端：

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # 按需修改配置后再启动
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 相关链接

- 仓库：https://github.com/hukun2005/ai-erp
- 安装包下载：https://github.com/hukun2005/ai-erp/releases/tag/v1.0.9
- 使用手册：仓库根目录 `ai-erp-user-manual-v1.4-20261106.pdf`