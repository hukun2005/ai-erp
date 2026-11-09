<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { templateApi, uploadApi } from '../api'
import { printDocument } from '../utils/print'
import { saveBlob } from '../utils/download'

const DOC_TYPES = [
  { value: 'sales_order', label: '销售订单' },
  { value: 'purchase_order', label: '采购订单' },
  { value: 'inbound', label: '入库单' },
  { value: 'sales_return', label: '销售退货单' },
  { value: 'purchase_return', label: '采购退货单' },
  { value: 'stocktake', label: '盘点单' },
  { value: 'statement', label: '供应商对账单' },
  { value: 'payment', label: '付款单' },
]

const COLUMN_DEFS = {
  sales_order: [
    { key: 'product_name', label: '商品名称' },
    { key: 'quantity', label: '数量', align: 'right' },
    { key: 'unit_price', label: '单价', align: 'right' },
    { key: 'amount', label: '金额', align: 'right' },
    { key: 'remark', label: '备注' },
  ],
  purchase_order: [
    { key: 'product_name', label: '商品名称' },
    { key: 'quantity', label: '数量', align: 'right' },
    { key: 'unit_price', label: '单价', align: 'right' },
    { key: 'amount', label: '金额', align: 'right' },
    { key: 'remark', label: '备注' },
  ],
  inbound: [
    { key: 'product_sku', label: 'SKU' },
    { key: 'product_name', label: '商品名称' },
    { key: 'quantity', label: '数量', align: 'right' },
    { key: 'unit_price', label: '单价', align: 'right' },
    { key: 'amount', label: '金额', align: 'right' },
    { key: 'batch_no', label: '批次号' },
    { key: 'remark', label: '备注' },
  ],
  sales_return: [
    { key: 'product_sku', label: 'SKU' },
    { key: 'product_name', label: '商品名称' },
    { key: 'quantity', label: '退货数量', align: 'right' },
    { key: 'unit_price', label: '单价', align: 'right' },
    { key: 'amount', label: '金额', align: 'right' },
  ],
  purchase_return: [
    { key: 'product_sku', label: 'SKU' },
    { key: 'product_name', label: '商品名称' },
    { key: 'quantity', label: '退货数量', align: 'right' },
    { key: 'unit_price', label: '单价', align: 'right' },
    { key: 'amount', label: '金额', align: 'right' },
  ],
  stocktake: [
    { key: 'product_sku', label: 'SKU' },
    { key: 'product_name', label: '商品名称' },
    { key: 'location_code', label: '库位' },
    { key: 'book_qty', label: '账面数量', align: 'right' },
    { key: 'actual_qty', label: '实盘数量', align: 'right' },
    { key: 'diff_qty', label: '差异', align: 'right' },
  ],
  statement: [
    { key: 'date', label: '日期' },
    { key: 'type', label: '类型' },
    { key: 'no', label: '单号' },
    { key: 'summary', label: '摘要' },
    { key: 'increase', label: '增加', align: 'right' },
    { key: 'decrease', label: '减少', align: 'right' },
    { key: 'balance', label: '余额', align: 'right' },
  ],
  payment: [
    { key: 'date', label: '日期' },
    { key: 'payment_no', label: '付款单号' },
    { key: 'method', label: '方式' },
    { key: 'amount', label: '金额', align: 'right' },
    { key: 'reference', label: '流水号' },
  ],
}

const SAMPLE_ROWS = {
  sales_order: { product_name: '演示商品', quantity: 10, unit_price: 88, amount: 880, remark: '测试行' },
  purchase_order: { product_name: '演示商品', quantity: 20, unit_price: 60, amount: 1200, remark: '测试行' },
  inbound: { product_sku: 'SKU-001', product_name: '演示商品', quantity: 20, unit_price: 60, amount: 1200, batch_no: 'B20261101', remark: '测试行' },
  sales_return: { product_sku: 'SKU-001', product_name: '演示商品', quantity: 2, unit_price: 88, amount: 176 },
  purchase_return: { product_sku: 'SKU-001', product_name: '演示商品', quantity: 3, unit_price: 60, amount: 180 },
  stocktake: { product_sku: 'SKU-001', product_name: '演示商品', location_code: 'A-01', book_qty: 100, actual_qty: 98, diff_qty: -2 },
  statement: { date: '2026-11-01', type: '采购入库', no: 'CG20261101001', summary: '演示商品等 2 项', increase: 1200, decrease: 0, balance: 1200 },
  payment: { date: '2026-11-02', payment_no: 'PY202611020001', method: '银行转账', amount: 1200, reference: '流水 20261102001' },
}

const loading = ref(false)
const list = ref([])
const filterType = ref('')
const saving = ref(false)
const editorVisible = ref(false)
const wordBusyId = ref(null)
const helpVisible = ref(false)

// Word 占位符清单：客户按这份约定在 Word 里写标记，打印时系统替换成真实数据
const WORD_PLACEHOLDERS = [
  {
    group: '单据信息',
    items: [
      ['order_no', '订单号'],
      ['order_date', '下单时间（含时分）'],
      ['order_day', '下单日期'],
      ['status', '状态英文值'],
      ['status_text', '状态中文'],
      ['created_by', '制单人'],
      ['created_at', '创建时间'],
    ],
  },
  {
    group: '客户信息',
    items: [
      ['customer_name', '客户名称'],
      ['customer_contact', '联系人'],
      ['customer_phone', '联系电话'],
      ['customer_email', '电子邮箱'],
      ['customer_address', '客户地址'],
      ['customer_level', '客户等级'],
      ['customer_credit_limit', '信用额度'],
    ],
  },
  {
    group: '供应商信息（采购单、入库单）',
    items: [
      ['supplier_name', '供应商名称'],
      ['supplier_contact', '联系人'],
      ['supplier_phone', '联系电话'],
      ['supplier_email', '电子邮箱'],
      ['supplier_address', '供应商地址'],
      ['supply_category', '供货类别'],
    ],
  },
  {
    group: '入库专用（order_no 在入库单中代表入库单号）',
    items: [
      ['warehouse_name', '入库仓库'],
      ['location_code', '库位'],
      ['purchase_order_no', '关联采购单号'],
      ['receipt_date', '入库日期'],
    ],
  },
  {
    group: '金额与数量',
    items: [
      ['item_count', '明细行数'],
      ['total_quantity', '商品总数量'],
      ['gross_amount', '商品金额'],
      ['discount_amount', '优惠金额'],
      ['total_amount', '应收合计'],
    ],
  },
  {
    group: '明细行（写在表格同一行内，系统按行复制）',
    items: [
      ['item.no', '序号'],
      ['item.sku', '商品编码'],
      ['item.name', '商品名称'],
      ['item.spec', '规格'],
      ['item.unit', '单位'],
      ['item.category', '分类'],
      ['item.quantity', '数量'],
      ['item.unit_price', '单价'],
      ['item.amount', '金额'],
      ['item.batch_no', '批次号（入库单）'],
      ['item.remark', '行备注（入库单）'],
    ],
  },
  {
    group: '其他',
    items: [
      ['remark', '备注'],
      ['company', '公司抬头'],
    ],
  },
].map((group) => ({
  ...group,
  items: group.items.map(([key, label]) => ({ key, label, syntax: `{{${key}}}` })),
}))

const editing = reactive({
  id: null,
  doc_type: 'sales_order',
  name: '',
  title: '',
  is_default: false,
  enabled: true,
  company: '',
  logo: '',
  show_logo: true,
  paper: 'A4',
  terms: '',
  signsText: '制单,审核,客户签收',
  columns: [],
})

const typeLabel = (value) => DOC_TYPES.find((item) => item.value === value)?.label || value
const currentDefs = computed(() => COLUMN_DEFS[editing.doc_type] || [])

async function load() {
  loading.value = true
  try {
    list.value = await templateApi.list({ doc_type: filterType.value })
  } finally {
    loading.value = false
  }
}

function resetColumns(docType, configColumns = {}) {
  editing.columns = (COLUMN_DEFS[docType] || []).map((def) => ({
    key: def.key,
    label: def.label,
    visible: configColumns[def.key] !== false,
    customLabel: typeof configColumns[def.key] === 'string' ? configColumns[def.key] : '',
  }))
}

function openCreate() {
  Object.assign(editing, {
    id: null,
    doc_type: 'sales_order',
    name: '标准销售订单',
    title: '销售订单',
    is_default: true,
    enabled: true,
    company: '示例公司',
    logo: '',
    show_logo: true,
    paper: 'A4',
    terms: '1. 请在收到单据后核对数量与金额。\n2. 如有异议请在 7 天内联系对接人。',
    signsText: '制单,审核,客户签收',
  })
  resetColumns('sales_order')
  editorVisible.value = true
}

function openEdit(row) {
  const cfg = row.config || {}
  Object.assign(editing, {
    id: row.id,
    doc_type: row.doc_type,
    name: row.name,
    title: row.title,
    is_default: row.is_default,
    enabled: row.enabled,
    company: cfg.company || '',
    logo: cfg.logo || '',
    show_logo: cfg.show_logo !== false,
    paper: cfg.paper === 'A5' ? 'A5' : 'A4',
    terms: cfg.terms || '',
    signsText: Array.isArray(cfg.signs) ? cfg.signs.join(',') : '制单,审核,客户签收',
  })
  resetColumns(row.doc_type, cfg.columns || {})
  editorVisible.value = true
}

function onTypeChange(value) {
  resetColumns(value)
  if (!editing.name || editing.is_default) {
    // 保持用户已填的名称，仅在新建议标题为空时同步
  }
  if (!editing.title) editing.title = typeLabel(value)
}

function buildConfig() {
  const columns = {}
  editing.columns.forEach((column) => {
    if (!column.visible) columns[column.key] = false
    else if (column.customLabel && column.customLabel !== column.label) {
      columns[column.key] = column.customLabel
    }
  })
  return {
    company: editing.company,
    logo: editing.logo,
    show_logo: editing.show_logo,
    paper: editing.paper,
    terms: editing.terms,
    signs: editing.signsText.split(/[,，]/).map((item) => item.trim()).filter(Boolean),
    columns,
  }
}

function payload(overrides = {}) {
  return {
    doc_type: editing.doc_type,
    name: editing.name.trim(),
    title: editing.title.trim() || editing.name.trim(),
    is_default: editing.is_default,
    enabled: editing.enabled,
    config: buildConfig(),
    ...overrides,
  }
}

async function save() {
  if (!editing.name.trim()) return ElMessage.warning('请填写模板名称')
  saving.value = true
  try {
    if (editing.id) await templateApi.update(editing.id, payload())
    else await templateApi.create(payload())
    ElMessage.success('模板已保存')
    editorVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function setDefault(row) {
  await templateApi.update(row.id, {
    doc_type: row.doc_type,
    name: row.name,
    title: row.title,
    is_default: true,
    enabled: true,
    config: row.config || {},
  })
  ElMessage.success(`已将「${row.name}」设为${typeLabel(row.doc_type)}默认模板`)
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除模板「${row.name}」吗？`, '删除模板', { type: 'warning' })
  await templateApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

// 客户下载后可以直接在 Word 中改成自己的版式，再上传回来作为初始格式
function downloadWordSample() {
  const docType = filterType.value || 'sales_order'
  const label = DOC_TYPES.find((item) => item.value === docType)?.label || '单据'
  templateApi
    .wordSample(docType)
    .then((blob) => saveBlob(blob, `${label}_Word初始格式示例.docx`))
    .catch(() => {
      // 错误提示由请求拦截器统一处理
    })
}

async function onWordUpload(row, uploadFile) {
  const file = uploadFile?.raw
  if (!file || !row.id) return
  if (!file.name.toLowerCase().endsWith('.docx')) {
    ElMessage.warning('请上传 .docx 格式的 Word 文件')
    return
  }
  wordBusyId.value = row.id
  try {
    await templateApi.uploadWord(row.id, file)
    ElMessage.success('Word 初始格式已上传，打印时将自动套用')
    await load()
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    wordBusyId.value = null
  }
}

async function removeWord(row) {
  await ElMessageBox.confirm(`确定移除「${row.name}」的 Word 初始格式吗？`, '移除 Word 模板', {
    type: 'warning',
  })
  await templateApi.removeWord(row.id)
  ElMessage.success('已移除，打印时将改用系统内置版式')
  load()
}

function printByTemplate(template, docType = editing.doc_type) {
  const cfg = template.config || {}
  const rows = [SAMPLE_ROWS[docType] || {}]
  if (!printDocument({
    title: typeLabel(docType),
    subtitle: '模板测试 · NO-TEST-001',
    meta: [
      ['往来单位', '示例客户/供应商'],
      ['日期', '2026-11-03'],
      ['制单人', '模板调试'],
      ['备注', '这是一张测试单据'],
    ],
    columns: COLUMN_DEFS[docType] || [],
    rows,
    totals: [{ label: '合计金额', value: `¥ ${rows[0].amount ?? 0}`, bold: true }],
    remark: '模板测试数据，确认版式后可正式打印。',
    template,
  })) {
    ElMessage.warning('浏览器拦截了打印窗口，请允许弹出窗口')
  }
}

function testPrint(row) {
  printByTemplate(row, row.doc_type)
}

function testEditing() {
  printByTemplate({ title: editing.title || editing.name, config: buildConfig() }, editing.doc_type)
}

async function onLogo(file) {
  if (!file.raw) return
  try {
    const result = await uploadApi.image(file.raw)
    editing.logo = result.url
    ElMessage.success('Logo 已上传')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '上传失败')
  }
}

function datetime(value) {
  return String(value || '').replace('T', ' ').slice(0, 16)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">单据模板</h1>
        <p class="page-subtitle">配置单据的抬头、Logo、列、条款与签字栏；这里也可以为客户上传 Word 初始格式（.docx），打印时自动把订单数据填进去。</p>
      </div>
      <el-button v-perm="'template:manage'" type="primary" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;新建模板
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-select v-model="filterType" placeholder="全部单据类型" clearable style="width: 180px" @change="load">
          <el-option v-for="item in DOC_TYPES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-button @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
        <el-button @click="downloadWordSample"><el-icon><Download /></el-icon>&nbsp;下载 Word 初始格式</el-button>
        <el-button @click="helpVisible = true">占位符说明</el-button>
        <span class="muted">同一单据类型可配置多套，打印时使用「默认」那一套；没有上传 Word 文件时沿用系统内置版式。</span>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="name" label="模板名称" min-width="160" />
        <el-table-column label="单据类型" width="130">
          <template #default="{ row }">{{ typeLabel(row.doc_type) }}</template>
        </el-table-column>
        <el-table-column prop="title" label="单据标题" width="150" />
        <el-table-column label="纸张" width="80" align="center">
          <template #default="{ row }">{{ row.config?.paper || 'A4' }}</template>
        </el-table-column>
        <el-table-column label="默认" width="80" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'primary' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Word 初始格式" width="170">
          <template #default="{ row }">
            <template v-if="row.word_filename">
              <el-tooltip :content="row.word_filename" placement="top">
                <span class="word-file">{{ row.word_filename }}</span>
              </el-tooltip>
              <el-button v-perm="'template:manage'" link type="danger" size="small" @click="removeWord(row)">移除</el-button>
            </template>
            <el-upload
              v-else
              v-perm="'template:manage'"
              :show-file-list="false"
              :auto-upload="false"
              accept=".docx"
              :on-change="(file) => onWordUpload(row, file)"
            >
              <el-button link type="primary" size="small" :loading="wordBusyId === row.id">上传 .docx</el-button>
            </el-upload>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="150">
          <template #default="{ row }">{{ datetime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="testPrint(row)">测试打印</el-button>
            <el-button v-perm="'template:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="!row.is_default" v-perm="'template:manage'" link type="warning" @click="setDefault(row)">设为默认</el-button>
            <el-button v-perm="'template:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="editorVisible" :title="editing.id ? '编辑单据模板' : '新建单据模板'" width="860px" top="5vh">
      <el-form label-width="92px">
        <div class="form-grid">
          <el-form-item label="单据类型" required>
            <el-select v-model="editing.doc_type" style="width: 100%" @change="onTypeChange">
              <el-option v-for="item in DOC_TYPES" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="模板名称" required>
            <el-input v-model="editing.name" placeholder="例如：标准销售订单" />
          </el-form-item>
          <el-form-item label="单据标题">
            <el-input v-model="editing.title" placeholder="留空则用模板名称" />
          </el-form-item>
          <el-form-item label="纸张">
            <el-select v-model="editing.paper" style="width: 100%">
              <el-option label="A4" value="A4" />
              <el-option label="A5" value="A5" />
            </el-select>
          </el-form-item>
          <el-form-item label="公司抬头">
            <el-input v-model="editing.company" placeholder="打印在单据顶部" />
          </el-form-item>
          <el-form-item label="Logo">
            <el-upload :show-file-list="false" :auto-upload="false" accept="image/*" :on-change="onLogo">
              <el-button size="small">上传图片</el-button>
            </el-upload>
            <span v-if="editing.logo" class="logo-preview">
              <img :src="editing.logo" alt="logo" />
              <el-button link type="danger" size="small" @click="editing.logo = ''">移除</el-button>
            </span>
          </el-form-item>
        </div>

        <el-form-item label="版式选项">
          <el-checkbox v-model="editing.show_logo">打印 Logo</el-checkbox>
          <el-checkbox v-model="editing.enabled">启用模板</el-checkbox>
          <el-checkbox v-model="editing.is_default">设为该类单据默认</el-checkbox>
        </el-form-item>

        <el-form-item label="显示列">
          <div class="column-list">
            <div v-for="column in editing.columns" :key="column.key" class="column-row">
              <el-checkbox v-model="column.visible">{{ column.label }}</el-checkbox>
              <el-input
                v-if="column.visible"
                v-model="column.customLabel"
                size="small"
                :placeholder="`列名（默认 ${column.label}）`"
                style="width: 190px"
              />
            </div>
          </div>
        </el-form-item>

        <el-form-item label="签字栏">
          <el-input v-model="editing.signsText" placeholder="逗号分隔，例如：制单,审核,客户签收" />
        </el-form-item>
        <el-form-item label="条款/说明">
          <el-input v-model="editing.terms" type="textarea" :rows="3" placeholder="打印在单据底部的付款条款、联系方式等" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button @click="testEditing">测试打印</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存模板</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="helpVisible" title="Word 初始格式占位符说明" width="780px" top="8vh">
      <el-alert type="info" :closable="false" show-icon>
        <p>1. 先选好单据类型，再点「下载 Word 初始格式」拿到示例 .docx，直接在 Word 里改成客户要的版式（销售、采购、入库均已支持）；</p>
        <p>2. 需要填数据的位置写成 <code v-pre>{{order_no}}</code> 这样的占位符，单头信息放在段落或单元格里即可；</p>
        <p>3. 商品明细的占位符必须写在表格的同一行里，系统会按明细行数自动复制这一行；</p>
        <p>4. 改好后在「Word 初始格式」列上传 .docx，打印时自动套用；没上传则用系统内置版式。</p>
      </el-alert>
      <div v-for="group in WORD_PLACEHOLDERS" :key="group.group" class="ph-group">
        <h4 class="ph-title">{{ group.group }}</h4>
        <div class="ph-grid">
          <div v-for="item in group.items" :key="item.key" class="ph-item">
            <code>{{ item.syntax }}</code>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="helpVisible = false">关闭</el-button>
        <el-button type="primary" @click="downloadWordSample">下载示例模板</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.muted {
  color: var(--text-secondary);
  font-size: 12px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 18px;
}

.word-file {
  display: inline-block;
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
  margin-right: 4px;
  font-size: 12px;
}

.ph-group {
  margin-top: 16px;
}

.ph-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
}

.ph-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 16px;
}

.ph-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.ph-item code {
  min-width: 172px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #f5f7fa;
  color: #1f6feb;
  font-family: Consolas, monospace;
}

.logo-preview {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 10px;
}

.logo-preview img {
  height: 30px;
  max-width: 90px;
  object-fit: contain;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 2px;
}

.column-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 16px;
  width: 100%;
}

.column-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
