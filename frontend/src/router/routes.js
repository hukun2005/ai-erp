/**
 * 工作台路由：meta.perm 控制菜单可见性与路由守卫；meta.permAny 表示任一权限即可。
 * meta.group 决定侧边栏一级分组，数组顺序即菜单顺序（按业务流程排列）。
 * meta.desc 用于命令面板与菜单提示，一句话说明这个页面能做什么。
 */
export const workspaceRoutes = [
  // ── 工作台：看板 → 待办 → 报表 ─────────────────────────
  {
    path: 'dashboard',
    name: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: {
      title: '经营看板',
      icon: 'DataBoard',
      perm: 'dashboard:view',
      group: '工作台',
      desc: '今日业绩、待办和趋势一屏掌握',
    },
  },
  {
    path: 'inbox',
    name: 'inbox',
    component: () => import('../views/Inbox.vue'),
    meta: {
      title: '消息中心',
      icon: 'Bell',
      group: '工作台',
      desc: '审批、超时与库存预警的站内消息',
    },
  },
  {
    path: 'approvals',
    name: 'approvals',
    component: () => import('../views/Approvals.vue'),
    meta: {
      title: '待办审批',
      icon: 'CircleCheck',
      permAny: ['sales:approve', 'purchase:approve'],
      group: '工作台',
      desc: '销售、采购订单待审批，通过后生效',
    },
  },
  {
    path: 'reports',
    name: 'reports',
    component: () => import('../views/Reports.vue'),
    meta: {
      title: '报表中心',
      icon: 'DataAnalysis',
      perm: 'report:view',
      group: '工作台',
      desc: '销售、库存、财务多维报表导出',
    },
  },

  // ── 销售：报价 → 订单 → 客户 → 退货 ───────────────────
  {
    path: 'quotations',
    name: 'quotations',
    component: () => import('../views/Quotations.vue'),
    meta: {
      title: '销售报价',
      icon: 'PriceTag',
      perm: 'quote:view',
      group: '销售',
      desc: '给客户报价，可一键转成销售订单',
    },
  },
  {
    path: 'sales-orders',
    name: 'sales-orders',
    component: () => import('../views/SalesOrders.vue'),
    meta: {
      title: '销售订单',
      icon: 'Sell',
      perm: 'sales:view',
      group: '销售',
      desc: '开单、提交审批、确认出库',
    },
  },
  {
    path: 'customers',
    name: 'customers',
    component: () => import('../views/Customers.vue'),
    meta: {
      title: '客户管理',
      icon: 'User',
      perm: 'customer:view',
      group: '销售',
      desc: '客户档案、联系人、信用额度',
    },
  },
  {
    path: 'returns',
    name: 'returns',
    component: () => import('../views/Returns.vue'),
    meta: {
      title: '退货管理',
      icon: 'RefreshLeft',
      perm: 'return:view',
      group: '销售',
      desc: '客户退货登记与退款处理',
    },
  },

  // ── 采购：申请 → 询价 → 订单 → 供应商 ─────────────────
  {
    path: 'requisitions',
    name: 'requisitions',
    component: () => import('../views/Requisitions.vue'),
    meta: {
      title: '采购申请',
      icon: 'DocumentAdd',
      perm: 'requisition:view',
      group: '采购',
      desc: '部门提采购需求，审批后转订单',
    },
  },
  {
    path: 'inquiries',
    name: 'inquiries',
    component: () => import('../views/Inquiries.vue'),
    meta: {
      title: '采购询价',
      icon: 'Search',
      perm: 'purchase:view',
      group: '采购',
      desc: '多家供应商比价后下单',
    },
  },
  {
    path: 'purchase-orders',
    name: 'purchase-orders',
    component: () => import('../views/PurchaseOrders.vue'),
    meta: {
      title: '采购订单',
      icon: 'ShoppingCart',
      perm: 'purchase:view',
      group: '采购',
      desc: '下单给供应商，收货入库',
    },
  },
  {
    path: 'suppliers',
    name: 'suppliers',
    component: () => import('../views/Suppliers.vue'),
    meta: {
      title: '供应商管理',
      icon: 'Van',
      perm: 'supplier:view',
      group: '采购',
      desc: '供应商档案与结算方式',
    },
  },

  // ── 生产 ──────────────────────────────────────────────
  {
    path: 'boms',
    name: 'boms',
    component: () => import('../views/Bom.vue'),
    meta: {
      title: 'BOM 物料清单',
      icon: 'Grid',
      perm: 'bom:view',
      group: '生产',
      desc: '产成品由哪些原材料组成',
    },
  },
  {
    path: 'production',
    name: 'production',
    component: () => import('../views/Production.vue'),
    meta: {
      title: '生产订单',
      icon: 'SetUp',
      perm: 'production:view',
      group: '生产',
      desc: '排产、领料、完工入库',
    },
  },

  // ── 库存：商品 → 库存 → 入库 → 调拨 → 调整 → 盘点 ─────
  {
    path: 'products',
    name: 'products',
    component: () => import('../views/Products.vue'),
    meta: {
      title: '商品管理',
      icon: 'Goods',
      perm: 'product:view',
      group: '库存',
      desc: '商品档案、价格与安全库存',
    },
  },
  {
    path: 'inventory',
    name: 'inventory',
    component: () => import('../views/Inventory.vue'),
    meta: {
      title: '库存管理',
      icon: 'Box',
      perm: 'inventory:view',
      group: '库存',
      desc: '实时库存、出入库流水',
    },
  },
  {
    path: 'inbounds',
    name: 'inbounds',
    component: () => import('../views/Inbound.vue'),
    meta: {
      title: '入库单',
      icon: 'Download',
      perm: 'inbound:view',
      group: '库存',
      desc: '采购收货、其他入库登记',
    },
  },
  {
    path: 'outbounds',
    name: 'outbounds',
    component: () => import('../views/Outbounds.vue'),
    meta: {
      title: '销售出库',
      icon: 'Van',
      perm: 'outbound:view',
      group: '库存',
      desc: '按销售订单分批发货、登记物流与签收',
    },
  },
  {
    path: 'transfers',
    name: 'transfers',
    component: () => import('../views/Transfers.vue'),
    meta: {
      title: '库存调拨',
      icon: 'Switch',
      perm: 'transfer:view',
      group: '库存',
      desc: '仓库之间调拨商品',
    },
  },
  {
    path: 'stock-adjustments',
    name: 'stock-adjustments',
    component: () => import('../views/StockAdjustments.vue'),
    meta: {
      title: '库存调整',
      icon: 'EditPen',
      perm: 'inventory:view',
      group: '库存',
      desc: '盈亏、报损等库存修正',
    },
  },
  {
    path: 'stocktakes',
    name: 'stocktakes',
    component: () => import('../views/Stocktakes.vue'),
    meta: {
      title: '库存盘点',
      icon: 'DocumentChecked',
      perm: 'stocktake:view',
      group: '库存',
      desc: '实盘数量与账面差异处理',
    },
  },

  // ── 财务 ──────────────────────────────────────────────
  {
    path: 'finance',
    name: 'finance',
    component: () => import('../views/Finance.vue'),
    meta: {
      title: '财务分析',
      icon: 'Money',
      perm: 'finance:view',
      group: '财务',
      desc: '收入、成本、毛利分析',
    },
  },
  {
    path: 'receivables',
    name: 'receivables',
    component: () => import('../views/Receivables.vue'),
    meta: {
      title: '应收与对账',
      icon: 'Tickets',
      perm: 'finance:view',
      group: '财务',
      desc: '客户欠款、对账单与收款',
    },
  },
  {
    path: 'payables',
    name: 'payables',
    component: () => import('../views/Payables.vue'),
    meta: {
      title: '应付与付款',
      icon: 'Wallet',
      perm: 'payable:view',
      group: '财务',
      desc: '供应商欠款与付款计划',
    },
  },
  {
    path: 'reconciliation',
    name: 'reconciliation',
    component: () => import('../views/Reconciliation.vue'),
    meta: {
      title: '对账总览',
      icon: 'DataLine',
      perm: 'finance:view',
      group: '财务',
      desc: '应收应付一站汇总与核销',
    },
  },
  {
    path: 'invoices',
    name: 'invoices',
    component: () => import('../views/Invoices.vue'),
    meta: {
      title: '发票管理',
      icon: 'Tickets',
      perm: 'invoice:view',
      group: '财务',
      desc: '销项进项发票登记与认证',
    },
  },
  {
    path: 'three-way-match',
    name: 'three-way-match',
    component: () => import('../views/ThreeWayMatch.vue'),
    meta: {
      title: '发票三单匹配',
      icon: 'Link',
      perm: 'invoice:view',
      group: '财务',
      desc: '采购订单、入库单、进项发票自动比对与差异预警',
    },
  },
  {
    path: 'pipeline-board',
    name: 'pipeline-board',
    component: () => import('../views/PipelineBoard.vue'),
    meta: {
      title: '链路看板',
      icon: 'TrendCharts',
      permAny: ['sales:view', 'report:view'],
      group: '销售',
      desc: '报价 → 订单 → 出库 → 开票 → 收款全链路进度',
    },
  },
  {
    path: 'ledger',
    name: 'ledger',
    component: () => import('../views/Ledger.vue'),
    meta: {
      title: '总账与报表',
      icon: 'Notebook',
      perm: 'ledger:view',
      group: '财务',
      desc: '会计科目、记账凭证与三大财务报表',
    },
  },
  {
    path: 'tax-report',
    name: 'tax-report',
    component: () => import('../views/TaxReport.vue'),
    meta: {
      title: '税务报表',
      icon: 'DataAnalysis',
      perm: 'finance:view',
      group: '财务',
      desc: '进项销项税额汇总与应纳税额',
    },
  },
  {
    path: 'bank-reconciliation',
    name: 'bank-reconciliation',
    component: () => import('../views/BankReconciliation.vue'),
    meta: {
      title: '银行对账',
      icon: 'Wallet',
      permAny: ['bank:view', 'finance:view'],
      group: '财务',
      desc: '导入银行流水，自动匹配回款与付款',
    },
  },

  // ── 智能 ──────────────────────────────────────────────
  {
    path: 'ai',
    name: 'ai',
    component: () => import('../views/AiAssistant.vue'),
    meta: {
      title: 'AI 经营助手',
      icon: 'MagicStick',
      perm: 'ai:assist',
      group: '智能',
      desc: '用一句话查数据、要建议、生成单据',
    },
  },
  {
    path: 'risk-radar',
    name: 'risk-radar',
    component: () => import('../views/RiskRadar.vue'),
    meta: {
      title: '风险雷达',
      icon: 'Aim',
      perm: 'dashboard:view',
      group: '智能',
      desc: '滞销、欠款、客户流失预警',
    },
  },
  {
    path: 'automations',
    name: 'automations',
    component: () => import('../views/Automation.vue'),
    meta: {
      title: '自动化规则',
      icon: 'SetUp',
      perm: 'automation:view',
      group: '智能',
      desc: '到点自动巡检、自动提醒',
    },
  },

  // ── 系统 ──────────────────────────────────────────────
  {
    path: 'warehouses',
    name: 'warehouses',
    component: () => import('../views/Warehouses.vue'),
    meta: {
      title: '仓库与库位',
      icon: 'OfficeBuilding',
      perm: 'warehouse:manage',
      group: '系统',
      desc: '仓库、库位编码维护',
    },
  },
  {
    path: 'templates',
    name: 'templates',
    component: () => import('../views/Template.vue'),
    meta: {
      title: '单据模板',
      icon: 'Document',
      perm: 'template:view',
      group: '系统',
      desc: '打印模板与单据样式',
    },
  },
  {
    path: 'data-exchange',
    name: 'data-exchange',
    component: () => import('../views/DataExchange.vue'),
    meta: {
      title: '数据交换',
      icon: 'Sort',
      perm: 'finance:view',
      group: '系统',
      desc: 'Excel 导入导出与数据迁移',
    },
  },
  {
    path: 'feishu',
    name: 'feishu',
    component: () => import('../views/FeishuNotify.vue'),
    meta: {
      title: '飞书通知',
      icon: 'ChatDotRound',
      perm: 'notify:manage',
      group: '系统',
      desc: '审批、预警推送到飞书群',
    },
  },
  {
    path: 'approval-settings',
    name: 'approval-settings',
    component: () => import('../views/ApprovalSettings.vue'),
    meta: {
      title: '审批与信用',
      icon: 'CircleCheck',
      permAny: ['approval:manage', 'credit:manage'],
      group: '系统',
      desc: '按金额分级审批、信用超额拦截与特批',
    },
  },
  {
    path: 'users',
    name: 'users',
    component: () => import('../views/Users.vue'),
    meta: {
      title: '用户与角色',
      icon: 'Setting',
      perm: 'user:manage',
      group: '系统',
      desc: '账号、角色与数据权限',
    },
  },
  {
    path: 'audit-logs',
    name: 'audit-logs',
    component: () => import('../views/AuditLogs.vue'),
    meta: {
      title: '审计日志',
      icon: 'Tickets',
      perm: 'audit:view',
      group: '系统',
      desc: '谁在什么时候改了什么',
    },
  },
  {
    path: 'backup',
    name: 'backup',
    component: () => import('../views/Backup.vue'),
    meta: {
      title: '数据备份',
      icon: 'FolderChecked',
      perm: 'user:manage',
      group: '系统',
      desc: '数据库一键备份、下载与恢复',
    },
  },
]

/** 导航分组元信息：顺序 + 一句话说明，供侧边栏渲染与命令面板分组使用 */
export const navGroupMeta = {
  工作台: { order: 0, hint: '每天从这里开始' },
  销售: { order: 1, hint: '报价、开单、客户、退货' },
  采购: { order: 2, hint: '申请、询价、下单、供应商' },
  生产: { order: 3, hint: '物料清单与生产订单' },
  库存: { order: 4, hint: '商品、库存、出入库' },
  财务: { order: 5, hint: '分析、应收、应付' },
  智能: { order: 6, hint: 'AI 助手与自动化' },
  系统: { order: 7, hint: '基础资料与权限' },
}
