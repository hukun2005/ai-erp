import http from './http'

/** 上传 Excel：统一走 multipart/form-data，返回后端逐行导入结果。 */
function upload(path, file) {
  const form = new FormData()
  form.append('file', file)
  return http.post(path, form, { headers: { 'Content-Type': 'multipart/form-data' } })
}

/** 上传 Excel 并下载后端返回的二进制文件（如错误报告）。 */
function uploadBlob(path, file) {
  const form = new FormData()
  form.append('file', file)
  return http.post(path, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    responseType: 'blob',
  })
}

export const authApi = {
  login: (data) => http.post('/auth/login', data),
  me: () => http.get('/auth/me'),
  sendRegisterCode: (phone) => http.post('/auth/register/send-code', { phone }),
  register: (data) => http.post('/auth/register', data),
}

export const productApi = {
  list: (params) => http.get('/products', { params }),
  categories: () => http.get('/products/categories'),
  scan: (code) => http.get('/products/scan', { params: { code } }),
  create: (data) => http.post('/products', data),
  quick: (data) => http.post('/products/quick', data),
  update: (id, data) => http.put(`/products/${id}`, data),
  remove: (id) => http.delete(`/products/${id}`),
  templateUrl: () => http.get('/products/import-template', { responseType: 'blob' }),
  import: (file) => upload('/products/import', file),
}

export const customerApi = {
  list: (params) => http.get('/customers', { params }),
  all: () => http.get('/customers/all'),
  create: (data) => http.post('/customers', data),
  update: (id, data) => http.put(`/customers/${id}`, data),
  remove: (id) => http.delete(`/customers/${id}`),
  templateUrl: () => http.get('/customers/import-template', { responseType: 'blob' }),
  import: (file) => upload('/customers/import', file),
}

export const supplierApi = {
  list: (params) => http.get('/suppliers', { params }),
  all: () => http.get('/suppliers/all'),
  create: (data) => http.post('/suppliers', data),
  update: (id, data) => http.put(`/suppliers/${id}`, data),
  remove: (id) => http.delete(`/suppliers/${id}`),
}

export const salesApi = {
  list: (params) => http.get('/sales-orders', { params }),
  detail: (id) => http.get(`/sales-orders/${id}`),
  create: (data) => http.post('/sales-orders', data),
  update: (id, data) => http.put(`/sales-orders/${id}`, data),
  changeStatus: (id, action, comment = '') => http.post(`/sales-orders/${id}/status`, { action, comment }),
  approvals: (id) => http.get(`/sales-orders/${id}/approvals`),
  printDocx: (id, params) => http.get(`/sales-orders/${id}/print.docx`, { params, responseType: 'blob' }),
  deliveryDocx: (id) => http.get(`/sales-orders/${id}/delivery.docx`, { responseType: 'blob' }),
  remove: (id) => http.delete(`/sales-orders/${id}`),
}

export const purchaseApi = {
  list: (params) => http.get('/purchase-orders', { params }),
  detail: (id) => http.get(`/purchase-orders/${id}`),
  create: (data) => http.post('/purchase-orders', data),
  update: (id, data) => http.put(`/purchase-orders/${id}`, data),
  changeStatus: (id, action, comment = '') => http.post(`/purchase-orders/${id}/status`, { action, comment }),
  approvals: (id) => http.get(`/purchase-orders/${id}/approvals`),
  printDocx: (id, params) => http.get(`/purchase-orders/${id}/print.docx`, { params, responseType: 'blob' }),
  remove: (id) => http.delete(`/purchase-orders/${id}`),
}

export const approvalApi = {
  pending: (params) => http.get('/approvals/pending', { params }),
  logs: (orderType, orderId) => http.get(`/approvals/${orderType}/${orderId}/logs`),
}

export const returnsApi = {
  salesList: (params) => http.get('/returns/sales', { params }),
  salesDetail: (id) => http.get(`/returns/sales/${id}`),
  salesCreate: (data) => http.post('/returns/sales', data),
  salesStatus: (id, action, comment = '') => http.post(`/returns/sales/${id}/status`, { action, comment }),
  salesRemove: (id) => http.delete(`/returns/sales/${id}`),
  salesAvailable: (orderId) => http.get(`/returns/sales/order/${orderId}/items`),
  purchaseList: (params) => http.get('/returns/purchase', { params }),
  purchaseDetail: (id) => http.get(`/returns/purchase/${id}`),
  purchaseCreate: (data) => http.post('/returns/purchase', data),
  purchaseStatus: (id, action, comment = '') => http.post(`/returns/purchase/${id}/status`, { action, comment }),
  purchaseRemove: (id) => http.delete(`/returns/purchase/${id}`),
  purchaseAvailable: (orderId) => http.get(`/returns/purchase/order/${orderId}/items`),
}

export const stocktakeApi = {
  list: (params) => http.get('/stocktakes', { params }),
  detail: (id, params) => http.get(`/stocktakes/${id}`, { params }),
  create: (data) => http.post('/stocktakes', data),
  saveItems: (id, items) => http.post(`/stocktakes/${id}/items`, { items }),
  changeStatus: (id, action, remark = '') => http.post(`/stocktakes/${id}/status`, { action, remark }),
  generateAdjustment: (id) => http.post(`/stocktakes/${id}/adjustment`),
  remove: (id) => http.delete(`/stocktakes/${id}`),
}

export const payablesApi = {
  summary: () => http.get('/payables'),
  openOrders: (supplierId) => http.get(`/payables/${supplierId}/orders`),
  payments: (params) => http.get('/supplier-payments', { params }),
  createPayment: (data) => http.post('/supplier-payments', data),
  voidPayment: (id, reason) => http.post(`/supplier-payments/${id}/void`, null, { params: { reason } }),
  approvePayment: (id, comment = '') => http.post(`/supplier-payments/${id}/approve`, null, { params: { comment } }),
  rejectPayment: (id, comment = '') => http.post(`/supplier-payments/${id}/reject`, null, { params: { comment } }),
  statements: (params) => http.get('/supplier-statements', { params }),
  createStatement: (data) => http.post('/supplier-statements', data),
  statementDetail: (id) => http.get(`/supplier-statements/${id}`),
  matchPayment: (data) => http.post('/supplier-payments/match', data),
  exportStatement: (id) => http.get(`/supplier-statements/${id}/export`, { responseType: 'blob' }),
}

export const dashboardApi = {
  summary: () => http.get('/dashboard/summary'),
  trend: (days = 30) => http.get('/dashboard/trend', { params: { days } }),
  topProducts: (limit = 8) => http.get('/dashboard/top-products', { params: { limit } }),
  categoryDistribution: () => http.get('/dashboard/category-distribution'),
  recentOrders: () => http.get('/dashboard/recent-orders'),
}

export const inventoryApi = {
  summary: () => http.get('/inventory/summary'),
  stock: (params) => http.get('/inventory/stock', { params }),
  exportStock: (params) => http.get('/inventory/stock/export', { params, responseType: 'blob' }),
  distribution: (params) => http.get('/inventory/distribution', { params }),
  exportDistribution: (params) =>
    http.get('/inventory/distribution/export', { params, responseType: 'blob' }),
  adjust: (data) => http.post('/inventory/adjust', data),
  logs: (params) => http.get('/inventory/logs', { params }),
  exportLogs: (params) => http.get('/inventory/logs/export', { params, responseType: 'blob' }),
  expiring: (days = 30) => http.get('/inventory/expiring', { params: { days } }),
}

export const transferApi = {
  list: (params) => http.get('/transfers', { params }),
  detail: (id) => http.get(`/transfers/${id}`),
  create: (data) => http.post('/transfers', data),
  changeStatus: (id, action) => http.post(`/transfers/${id}/status`, { action }),
  remove: (id) => http.delete(`/transfers/${id}`),
  stats: () => http.get('/transfers/stats'),
  sourceStock: (params) => http.get('/transfers/source-stock', { params }),
}

export const warehouseApi = {
  list: (params) => http.get('/warehouses', { params }),
  options: () => http.get('/warehouses/options'),
  create: (data) => http.post('/warehouses', data),
  update: (id, data) => http.put(`/warehouses/${id}`, data),
  remove: (id) => http.delete(`/warehouses/${id}`),
  createLocation: (warehouseId, data) => http.post(`/warehouses/${warehouseId}/locations`, data),
  updateLocation: (warehouseId, locationId, data) =>
    http.put(`/warehouses/${warehouseId}/locations/${locationId}`, data),
  removeLocation: (warehouseId, locationId) =>
    http.delete(`/warehouses/${warehouseId}/locations/${locationId}`),
}

export const notificationApi = {
  status: () => http.get('/notifications/status'),
  config: () => http.get('/notifications/config'),
  saveConfig: (data) => http.put('/notifications/config', data),
  clearConfig: () => http.delete('/notifications/config'),
  testConfig: (data) => http.post('/notifications/config/test', data || {}),
  alertRule: () => http.get('/notifications/alert-rule'),
  saveAlertRule: (data) => http.put('/notifications/alert-rule', data),
  preview: (params) => http.get('/notifications/preview', { params }),
  send: (data) => http.post('/notifications/send', data),
  test: () => http.post('/notifications/test'),
}

export const inboxApi = {
  list: (params) => http.get('/inbox', { params }),
  unreadCount: () => http.get('/inbox/unread-count'),
  markRead: (id) => http.post(`/inbox/${id}/read`),
  markAllRead: () => http.post('/inbox/read-all'),
}

export const financeApi = {
  overview: (days = 30) => http.get('/finance/overview', { params: { days } }),
  trend: (days = 30) => http.get('/finance/trend', { params: { days } }),
  receivables: () => http.get('/finance/receivables'),
}

export const ledgerApi = {
  accounts: () => http.get('/ledger/accounts'),
  createAccount: (data) => http.post('/ledger/accounts', data),
  vouchers: (params) => http.get('/ledger/vouchers', { params }),
  createVoucher: (data) => http.post('/ledger/vouchers', data),
  voidVoucher: (id) => http.post(`/ledger/vouchers/${id}/void`),
  generate: (period) => http.post('/ledger/vouchers/generate', null, { params: { period } }),
  balanceSheet: (period) => http.get('/ledger/statements/balance-sheet', { params: { period } }),
  incomeStatement: (period) => http.get('/ledger/statements/income-statement', { params: { period } }),
  cashFlow: (period) => http.get('/ledger/statements/cash-flow', { params: { period } }),
}

export const userApi = {
  list: (params) => http.get('/users', { params }),
  roles: () => http.get('/users/roles'),
  create: (data) => http.post('/users', data),
  update: (id, data) => http.put(`/users/${id}`, data),
  resetPassword: (id, password) => http.post(`/users/${id}/reset-password`, { password }),
  remove: (id) => http.delete(`/users/${id}`),
  sendCode: (phone) => http.post('/users/send-code', { phone }),
  registerByPhone: (data) => http.post('/users/register', data),
}

export const aiApi = {
  status: () => http.get('/ai/status'),
  insights: () => http.get('/ai/insights'),
  replenishment: (leadTime = 15) => http.get('/ai/replenishment', { params: { lead_time: leadTime } }),
  forecast: (params) => http.get('/ai/forecast', { params }),
  chat: (data) => http.post('/ai/chat', data),
  createPurchase: (productIds) => http.post('/ai/replenishment/create-purchase', { product_ids: productIds || null }),
}

export const auditApi = {
  summary: () => http.get('/audit-logs/summary'),
  actions: () => http.get('/audit-logs/actions'),
  list: (params) => http.get('/audit-logs', { params }),
}

export const receivablesApi = {
  summary: () => http.get('/receivables'),
  openOrders: (customerId) => http.get(`/receivables/${customerId}/orders`),
  payments: (params) => http.get('/payments', { params }),
  createPayment: (data) => http.post('/payments', data),
  voidPayment: (id, reason) => http.post(`/payments/${id}/void`, null, { params: { reason } }),
  approvePayment: (id, comment = '') => http.post(`/payments/${id}/approve`, null, { params: { comment } }),
  rejectPayment: (id, comment = '') => http.post(`/payments/${id}/reject`, null, { params: { comment } }),
  statements: (params) => http.get('/statements', { params }),
  createStatement: (data) => http.post('/statements', data),
  statementDetail: (id) => http.get(`/statements/${id}`),
  matchPayment: (data) => http.post('/payments/match', data),
  exportStatement: (id) => http.get(`/statements/${id}/export`, { responseType: 'blob' }),
}

export const templateApi = {
  list: (params) => http.get('/templates', { params }),
  defaultFor: (docType) => http.get('/templates/default', { params: { doc_type: docType } }),
  create: (data) => http.post('/templates', data),
  update: (id, data) => http.put(`/templates/${id}`, data),
  remove: (id) => http.delete(`/templates/${id}`),
  uploadWord: (id, file) => upload(`/templates/${id}/word`, file),
  removeWord: (id) => http.delete(`/templates/${id}/word`),
  wordSample: (docType = 'sales_order') =>
    http.get('/templates/word-sample', { params: { doc_type: docType }, responseType: 'blob' }),
}

export const inboundApi = {
  list: (params) => http.get('/inbounds', { params }),
  detail: (id) => http.get(`/inbounds/${id}`),
  create: (data) => http.post('/inbounds', data),
  update: (id, data) => http.put(`/inbounds/${id}`, data),
  changeStatus: (id, action, remark = '') =>
    http.post(`/inbounds/${id}/status`, { action, remark }),
  printDocx: (id, params) => http.get(`/inbounds/${id}/print.docx`, { params, responseType: 'blob' }),
  remove: (id) => http.delete(`/inbounds/${id}`),
  recognize: (file) => upload('/inbounds/recognize', file),
}

export const uploadApi = {
  image: (file) => upload('/uploads/image', file),
}

/** 单据照片识别：一次可传多页，识别可能超过默认 20 秒，单独放宽超时。 */
export const visionApi = {
  status: () => http.get('/vision/status'),
  recognize: (docType, files) => {
    const form = new FormData()
    form.append('doc_type', docType)
    for (const file of files) form.append('files', file)
    return http.post('/vision/recognize', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 180000,
    })
  },
}

export const riskApi = {
  radar: () => http.get('/risk-radar'),
}

export const searchApi = {
  search: (q) => http.get('/search', { params: { q } }),
}

export const automationApi = {
  options: () => http.get('/automations/options'),
  list: () => http.get('/automations'),
  create: (data) => http.post('/automations', data),
  update: (id, data) => http.put(`/automations/${id}`, data),
  remove: (id) => http.delete(`/automations/${id}`),
  run: (id, dryRun = false) => http.post(`/automations/${id}/run`, { dry_run: dryRun }),
  runs: (id) => http.get(`/automations/${id}/runs`),
  recentRuns: (limit = 20) => http.get('/automations/runs', { params: { limit } }),
}

export const creditApi = {
  detail: (customerId) => http.get(`/customers/${customerId}/credit`),
}

export const bomApi = {
  list: (params) => http.get('/boms', { params }),
  detail: (id) => http.get(`/boms/${id}`),
  activeFor: (productId) => http.get(`/boms/active/${productId}`),
  create: (data) => http.post('/boms', data),
  update: (id, data) => http.put(`/boms/${id}`, data),
  remove: (id) => http.delete(`/boms/${id}`),
}

export const productionApi = {
  list: (params) => http.get('/production-orders', { params }),
  detail: (id) => http.get(`/production-orders/${id}`),
  create: (data) => http.post('/production-orders', data),
  changeStatus: (id, action, remark = '') =>
    http.post(`/production-orders/${id}/status`, { action, remark }),
  issue: (id, data) => http.post(`/production-orders/${id}/issue`, data),
  scanProcess: (id, processId, operator = '') =>
    http.post(`/production-orders/${id}/processes/${processId}/scan`, { operator }),
  addExpense: (id, data) => http.post(`/production-orders/${id}/expenses`, data),
  removeExpense: (id, expenseId) => http.delete(`/production-orders/${id}/expenses/${expenseId}`),
  remove: (id) => http.delete(`/production-orders/${id}`),
}

export const quoteApi = {
  list: (params) => http.get('/quotations', { params }),
  detail: (id) => http.get(`/quotations/${id}`),
  create: (data) => http.post('/quotations', data),
  update: (id, data) => http.put(`/quotations/${id}`, data),
  changeStatus: (id, status) => http.post(`/quotations/${id}/status`, { status }),
  convert: (id) => http.post(`/quotations/${id}/convert`),
  remove: (id) => http.delete(`/quotations/${id}`),
}

export const requisitionApi = {
  list: (params) => http.get('/purchase-requisitions', { params }),
  detail: (id) => http.get(`/purchase-requisitions/${id}`),
  create: (data) => http.post('/purchase-requisitions', data),
  update: (id, data) => http.put(`/purchase-requisitions/${id}`, data),
  changeStatus: (id, action, comment = '') =>
    http.post(`/purchase-requisitions/${id}/status`, { action, comment }),
  convert: (id, supplierId) =>
    http.post(`/purchase-requisitions/${id}/convert`, { supplier_id: supplierId }),
  remove: (id) => http.delete(`/purchase-requisitions/${id}`),
}

export const inquiryApi = {
  list: (params) => http.get('/purchase-inquiries', { params }),
  detail: (id) => http.get(`/purchase-inquiries/${id}`),
  create: (data) => http.post('/purchase-inquiries', data),
  addQuote: (id, data) => http.post(`/purchase-inquiries/${id}/quotes`, data),
  removeQuote: (id, quoteId) => http.delete(`/purchase-inquiries/${id}/quotes/${quoteId}`),
  convert: (id, quoteId) => http.post(`/purchase-inquiries/${id}/convert`, { quote_id: quoteId }),
  remove: (id) => http.delete(`/purchase-inquiries/${id}`),
}

export const adjustmentApi = {
  list: (params) => http.get('/stock-adjustments', { params }),
  detail: (id) => http.get(`/stock-adjustments/${id}`),
  create: (data) => http.post('/stock-adjustments', data),
  changeStatus: (id, action, remark = '') =>
    http.post(`/stock-adjustments/${id}/status`, { action, remark }),
  remove: (id) => http.delete(`/stock-adjustments/${id}`),
}

export const reportApi = {
  overview: (params) => http.get('/reports/overview', { params }),
  sales: (params) => http.get('/reports/sales', { params }),
  purchase: (params) => http.get('/reports/purchase', { params }),
  production: (params) => http.get('/reports/production', { params }),
}

export const exchangeApi = {
  overview: () => http.get('/data-exchange/overview'),
  logs: (limit = 10) => http.get('/data-exchange/logs', { params: { limit } }),
  templateUrl: (dataset) => http.get(`/data-exchange/templates/${dataset}`, { responseType: 'blob' }),
  import: (dataset, file) => upload(`/data-exchange/opening-import/${dataset}`, file),
  preview: (dataset, file) => upload(`/data-exchange/opening-import/${dataset}/preview`, file),
  errorReport: (dataset, file) => uploadBlob(`/data-exchange/opening-import/${dataset}/error-report`, file),
  export: (params) => http.get('/data-exchange/export', { params, responseType: 'blob' }),
}

export const backupApi = {
  list: () => http.get('/backups'),
  create: () => http.post('/backups'),
  download: (name) => http.get(`/backups/${name}/download`, { responseType: 'blob' }),
  restore: (name) => http.post(`/backups/${name}/restore`, { confirm: true }),
  remove: (name) => http.delete(`/backups/${name}`),
}

export const invoiceApi = {
  list: (params) => http.get('/invoices', { params }),
  summary: () => http.get('/invoices/summary'),
  threeWay: () => http.get('/invoices/three-way'),
  create: (data) => http.post('/invoices', data),
  update: (id, data) => http.put(`/invoices/${id}`, data),
  changeStatus: (id, action) => http.post(`/invoices/${id}/status`, { action }),
  remove: (id) => http.delete(`/invoices/${id}`),
  batchRemove: (ids) => http.post('/invoices/batch-delete', { ids }),
  batchUpdate: (ids, data) => http.post('/invoices/batch-update', { ids, data }),
  batchExport: (ids) => http.post('/invoices/batch-export', { ids }, { responseType: 'blob' }),
  batchPrint: (ids) => http.post('/invoices/batch-print', { ids }, { responseType: 'blob' }),
  batchReview: (ids) => http.post('/invoices/batch-review', { ids }),
  template: () => http.get('/invoices/template', { responseType: 'blob' }),
  import: (file) => upload('/invoices/import', file),
}

export const reconciliationApi = {
  overview: () => http.get('/reconciliation/overview'),
}

export const periodApi = {
  list: () => http.get('/period'),
  summary: (period) => http.get(`/period/${period}/summary`),
  check: (period) => http.get(`/period/${period}/check`),
  close: (period) => http.post('/period/close', { period }),
  reopen: (period) => http.post('/period/reopen', { period }),
}

export const pipelineApi = {
  salesBoard: () => http.get('/pipeline/sales-board'),
}

export const taxApi = {
  report: (period = '') => http.get('/tax/report', { params: { period } }),
  detail: (params) => http.get('/tax/detail', { params }),
}

export const bankApi = {
  statements: (params) => http.get('/bank/statements', { params }),
  matches: (params) => http.get('/bank/matches', { params }),
  templateUrl: () => http.get('/bank/template', { responseType: 'blob' }),
  import: (file) => upload('/bank/import', file),
  applyMatch: (id) => http.post(`/bank/matches/${id}/apply`),
  dismissMatch: (id) => http.post(`/bank/matches/${id}/dismiss`),
}

export const outboundApi = {
  list: (params) => http.get('/outbounds', { params }),
  get: (id) => http.get(`/outbounds/${id}`),
  create: (data) => http.post('/outbounds', data),
  update: (id, data) => http.put(`/outbounds/${id}`, data),
  changeStatus: (id, payload) => http.post(`/outbounds/${id}/status`, payload),
  scan: (id, code) => http.post(`/outbounds/${id}/scan`, null, { params: { code } }),
  remove: (id) => http.delete(`/outbounds/${id}`),
  progress: (orderId) => http.get(`/outbounds/progress/${orderId}`),
}

export const approvalRuleApi = {
  list: (params) => http.get('/approval-rules', { params }),
  options: () => http.get('/approval-rules/options'),
  templates: () => http.get('/approval-rules/templates'),
  applyTemplate: (key, replace = true) =>
    http.post(`/approval-rules/templates/${key}/apply`, null, { params: { replace } }),
  sla: () => http.get('/approval-rules/sla'),
  saveSla: (docType, data) => http.put(`/approval-rules/sla/${docType}`, data),
  scanSla: () => http.post('/approval-rules/sla/scan'),
  preview: (doc_type, amount, extra = {}) =>
    http.get('/approval-rules/preview', { params: { doc_type, amount, ...extra } }),
  create: (data) => http.post('/approval-rules', data),
  update: (id, data) => http.put(`/approval-rules/${id}`, data),
  remove: (id) => http.delete(`/approval-rules/${id}`),
  seedExample: () => http.post('/approval-rules/seed-example'),
  delegations: (params) => http.get('/approval-rules/delegations', { params }),
  createDelegation: (data) => http.post('/approval-rules/delegations', data),
  updateDelegation: (id, data) => http.put(`/approval-rules/delegations/${id}`, data),
  removeDelegation: (id) => http.delete(`/approval-rules/delegations/${id}`),
}

export const creditAdminApi = {
  config: () => http.get('/credit/config'),
  saveConfig: (policies) => http.put('/credit/config', { policies }),
  overrides: (params) => http.get('/credit/overrides', { params }),
  requestOverride: (order_id, reason = '') => http.post('/credit/overrides', { order_id, reason }),
  decideOverride: (id, action, comment = '') => http.post(`/credit/overrides/${id}/status`, { action, comment }),
  summary: (customerId) => http.get(`/credit/summary/${customerId}`),
}
export const printApi = {
  // 返回 Blob：PDF 需要带鉴权头，不能用 <a href> 直链
  pdf: (docType, id, params) =>
    http.get(`/print/${docType}/${id}/pdf`, { params, responseType: 'blob' }),
  batch: (payload) => http.post('/print/batch', payload, { responseType: 'blob' }),
  calibration: (params) => http.get('/print/calibration/sheet', { params, responseType: 'blob' }),
  jobs: (params) => http.get('/print/jobs', { params }),
}
