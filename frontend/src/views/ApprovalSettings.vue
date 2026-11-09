<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approvalRuleApi, creditAdminApi } from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const tab = ref('rules')

const loading = ref(false)
const rules = ref([])
const docType = ref('sales')
const warehouses = ref([])
const productCategories = ref([])
const templates = ref([])
const selectedTemplate = ref('')
const previewLevel = ref('')
const previewWarehouse = ref(null)
const amountPreview = ref(12000)
const preview = ref(null)

const dialogVisible = ref(false)
const editingId = ref(null)
const saving = ref(false)
const form = reactive({
  doc_type: 'sales',
  name: '',
  enabled: true,
  min_amount: 0,
  max_amount: null,
  level: 1,
  permission: 'sales:approve',
  level_name: '',
  mode: 'single',
  approvers: '',
})

// 条件分支：只对命中的单据生效（与金额区间同时满足）
const conditionForm = reactive({
  levels: [],
  creditExceeded: false,
  minMargin: null,
  warehouseIds: [],
  createdBy: '',
  categories: [],
})

function resetConditions() {
  conditionForm.levels = []
  conditionForm.creditExceeded = false
  conditionForm.minMargin = null
  conditionForm.warehouseIds = []
  conditionForm.createdBy = ''
  conditionForm.categories = []
}

function buildConditions() {
  const conditions = {}
  if (conditionForm.levels.length) conditions.customer_level = [...conditionForm.levels]
  if (conditionForm.creditExceeded) conditions.credit_exceeded = true
  if (conditionForm.minMargin !== null && conditionForm.minMargin !== '') {
    conditions.min_margin = Number(conditionForm.minMargin) / 100
  }
  if (conditionForm.warehouseIds.length) conditions.warehouse_id = [...conditionForm.warehouseIds]
  if (conditionForm.categories.length) conditions.product_category = [...conditionForm.categories]
  const creators = String(conditionForm.createdBy || '')
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean)
  if (creators.length) conditions.created_by = creators
  return conditions
}

function applyConditions(raw) {
  resetConditions()
  if (!raw) return
  let data = raw
  if (typeof raw === 'string') {
    try {
      data = JSON.parse(raw)
    } catch {
      data = {}
    }
  }
  if (!data || typeof data !== 'object') return
  if (Array.isArray(data.customer_level)) conditionForm.levels = [...data.customer_level]
  if (data.credit_exceeded) conditionForm.creditExceeded = true
  if (data.min_margin !== undefined) conditionForm.minMargin = Math.round(Number(data.min_margin) * 100)
  if (data.warehouse_id) {
    conditionForm.warehouseIds = (Array.isArray(data.warehouse_id) ? data.warehouse_id : [data.warehouse_id]).map(Number)
  }
  if (data.created_by) {
    conditionForm.createdBy = (Array.isArray(data.created_by) ? data.created_by : [data.created_by]).join(',')
  }
  if (data.product_category) {
    conditionForm.categories = Array.isArray(data.product_category)
      ? [...data.product_category]
      : [data.product_category]
  }
}

const slaRows = ref([])
const slaLoading = ref(false)
const slaSaving = ref('')
const slaScanResult = ref(null)

async function loadSla() {
  slaLoading.value = true
  try {
    const items = (await approvalRuleApi.sla()).items
    items.forEach((row) => {
      row.escalateOn = row.escalate_hours != null
    })
    slaRows.value = items
  } finally {
    slaLoading.value = false
  }
}

function toggleEscalate(row, on) {
  row.escalateOn = on
  if (on) {
    if (row.escalate_hours == null || row.escalate_hours <= row.remind_hours) {
      row.escalate_hours = Math.min(row.remind_hours + 24, 720)
    }
  } else {
    row.escalate_hours = null
  }
}

async function saveSla(row) {
  slaSaving.value = row.doc_type
  try {
    if (row.escalateOn && (row.escalate_hours == null || row.escalate_hours <= row.remind_hours)) {
      ElMessage.warning('升级小时数需大于提醒小时数')
      return
    }
    await approvalRuleApi.saveSla(row.doc_type, {
      remind_hours: row.remind_hours,
      escalate_hours: row.escalateOn ? row.escalate_hours : null,
      enabled: row.enabled,
    })
    ElMessage.success(`已保存「${row.label}」的超时规则`)
    await loadSla()
  } finally {
    slaSaving.value = ''
  }
}

async function scanSlaNow() {
  slaScanResult.value = await approvalRuleApi.scanSla()
  ElMessage.success(
    `巡检完成：待审批 ${slaScanResult.value.checked} 条，提醒 ${slaScanResult.value.reminded} 条，升级 ${slaScanResult.value.escalated} 条`
  )
}

async function loadOptions() {
  try {
    const data = await approvalRuleApi.options()
    warehouses.value = data.warehouses || []
    productCategories.value = data.product_categories || []
  } catch {
    warehouses.value = []
    productCategories.value = []
  }
  try {
    templates.value = await approvalRuleApi.templates()
  } catch {
    templates.value = []
  }
}

async function applyTemplate() {
  const template = templates.value.find((item) => item.key === selectedTemplate.value)
  if (!template) {
    ElMessage.warning('请先选择模板')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将覆盖「${template.doc_types.map((t) => docTypeLabel[t] || t).join('、')}」下的现有规则，确定套用「${template.name}」吗？`,
      '套用审批模板',
      { type: 'warning' }
    )
  } catch {
    return
  }
  const result = await approvalRuleApi.applyTemplate(template.key)
  ElMessage.success(`已套用「${result.name}」，新增 ${result.created} 条规则`)
  await loadRules()
}

const MODE_OPTIONS = [
  { value: 'single', label: '单人审批（按权限）' },
  { value: 'or', label: '或签（名单中任一通过）' },
  { value: 'counter', label: '会签（名单中全部通过）' },
]

const delegations = ref([])
const delegationLoading = ref(false)
const delegationDialog = ref(false)
const delegationEditingId = ref(null)
const delegationSaving = ref(false)
const delegationForm = reactive({ doc_type: 'sales', delegator: '', delegatee: '', enabled: true })

const policies = reactive({ '*': 'warn', A: 'warn', B: 'warn', C: 'warn' })
const policyOptions = [
  { value: 'off', label: '不检查' },
  { value: 'warn', label: '仅提示' },
  { value: 'block', label: '超额拦截' },
]
const overrides = ref([])
const overrideLoading = ref(false)

const canManageRules = computed(() => userStore.hasPerm('approval:manage'))
const canManageCredit = computed(() => userStore.hasPerm('credit:manage'))

const docTypeLabel = {
  sales: '销售订单',
  purchase: '采购订单',
  outbound: '销售出库单',
  sales_return: '销售退货单',
  purchase_return: '采购退货单',
  payment: '收款单',
  supplier_payment: '付款单',
  requisition: '采购申请',
  stocktake: '盘点单',
}
const DOC_DEFAULT_PERMISSION = {
  sales: 'sales:approve',
  purchase: 'purchase:approve',
  outbound: 'outbound:approve',
  sales_return: 'return:approve',
  purchase_return: 'return:approve',
  payment: 'payable:approve',
  supplier_payment: 'payable:approve',
  requisition: 'requisition:approve',
  stocktake: 'stocktake:approve',
}
const docTypeOptions = Object.entries(docTypeLabel).map(([value, label]) => ({ value, label }))
const permissionOptions = [
  { value: 'sales:approve', label: '销售审批（sales:approve）' },
  { value: 'purchase:approve', label: '采购审批（purchase:approve）' },
  { value: 'outbound:approve', label: '出库审批（outbound:approve）' },
  { value: 'return:approve', label: '退货审批（return:approve）' },
  { value: 'requisition:approve', label: '采购申请审批（requisition:approve）' },
  { value: 'stocktake:approve', label: '盘点审批（stocktake:approve）' },
  { value: 'payable:approve', label: '付款审批（payable:approve）' },
  { value: 'finance:manage', label: '财务操作（finance:manage）' },
  { value: 'payable:manage', label: '付款与对账（payable:manage）' },
]

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function rangeText(row) {
  const min = money(row.min_amount)
  return row.max_amount == null ? `≥ ¥${min}` : `¥${min} ~ ¥${money(row.max_amount)}`
}

async function loadRules() {
  loading.value = true
  try {
    rules.value = await approvalRuleApi.list({ doc_type: docType.value })
    await loadPreview()
  } finally {
    loading.value = false
  }
}

async function loadPreview() {
  try {
    preview.value = await approvalRuleApi.preview(docType.value, amountPreview.value || 0, {
      customer_level: previewLevel.value || undefined,
      warehouse_id: previewWarehouse.value || undefined,
    })
  } catch {
    preview.value = null
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    doc_type: docType.value,
    name: '',
    enabled: true,
    min_amount: 0,
    max_amount: null,
    level: 1,
    permission: DOC_DEFAULT_PERMISSION[docType.value] || 'sales:approve',
    level_name: '',
    mode: 'single',
    approvers: '',
  })
  resetConditions()
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    doc_type: row.doc_type,
    name: row.name,
    enabled: row.enabled,
    min_amount: row.min_amount,
    max_amount: row.max_amount,
    level: row.level,
    permission: row.permission,
    level_name: row.level_name,
    mode: row.mode || 'single',
    approvers: row.approvers || '',
  })
  applyConditions(row.conditions)
  dialogVisible.value = true
}

async function saveRule() {
  if (saving.value) return
  saving.value = true
  try {
    const payload = { ...form, conditions: buildConditions() }
    if (editingId.value) {
      await approvalRuleApi.update(editingId.value, payload)
      ElMessage.success('规则已更新')
    } else {
      await approvalRuleApi.create(payload)
      ElMessage.success('规则已创建')
    }
    dialogVisible.value = false
    await loadRules()
  } finally {
    saving.value = false
  }
}

async function removeRule(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.name || row.level + ' 级'}」吗？`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  await approvalRuleApi.remove(row.id)
  ElMessage.success('已删除')
  loadRules()
}

async function seedExample() {
  const result = await approvalRuleApi.seedExample()
  ElMessage.success(result?.created ? '已生成示例规则' : '已有规则，未做改动')
  loadRules()
}

async function loadDelegations() {
  delegationLoading.value = true
  try {
    delegations.value = await approvalRuleApi.delegations({})
  } finally {
    delegationLoading.value = false
  }
}

function openDelegationCreate() {
  delegationEditingId.value = null
  Object.assign(delegationForm, { doc_type: 'sales', delegator: '', delegatee: '', enabled: true })
  delegationDialog.value = true
}

function openDelegationEdit(row) {
  delegationEditingId.value = row.id
  Object.assign(delegationForm, {
    doc_type: row.doc_type,
    delegator: row.delegator,
    delegatee: row.delegatee,
    enabled: row.enabled,
  })
  delegationDialog.value = true
}

async function saveDelegation() {
  if (delegationSaving.value) return
  delegationSaving.value = true
  try {
    if (delegationEditingId.value) {
      await approvalRuleApi.updateDelegation(delegationEditingId.value, { ...delegationForm })
      ElMessage.success('委托已更新')
    } else {
      await approvalRuleApi.createDelegation({ ...delegationForm })
      ElMessage.success('委托已创建')
    }
    delegationDialog.value = false
    await loadDelegations()
  } finally {
    delegationSaving.value = false
  }
}

async function removeDelegation(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.delegator} → ${row.delegatee}」的委托吗？`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  await approvalRuleApi.removeDelegation(row.id)
  ElMessage.success('已删除')
  loadDelegations()
}

async function loadConfig() {
  if (!canManageCredit.value) return
  const config = await creditAdminApi.config()
  policies['*'] = config.default || 'warn'
  for (const level of ['A', 'B', 'C']) {
    policies[level] = config.levels?.[level] || config.default || 'warn'
  }
}

async function saveConfig() {
  await creditAdminApi.saveConfig({ ...policies })
  ElMessage.success('信用策略已保存')
}

async function loadOverrides() {
  if (!canManageCredit.value) return
  overrideLoading.value = true
  try {
    const data = await creditAdminApi.overrides({ page_size: 50 })
    overrides.value = data.items || []
  } finally {
    overrideLoading.value = false
  }
}

async function decide(row, action) {
  await creditAdminApi.decideOverride(row.id, action)
  ElMessage.success(action === 'approve' ? '已通过' : '已驳回')
  loadOverrides()
}

onMounted(async () => {
  await loadOptions()
  await loadRules()
  await loadSla()
  await loadDelegations()
  await loadConfig()
  await loadOverrides()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">审批与信用</h1>
        <p class="page-subtitle">
          按金额分级审批，按客户等级控制信用超额；超额单可申请特批，通过后放行这一单
        </p>
      </div>
    </div>

    <el-tabs v-model="tab" class="card">
      <el-tab-pane label="审批规则" name="rules">
        <div class="toolbar">
          <el-select v-model="docType" filterable style="width: 180px" @change="loadRules">
            <el-option v-for="item in docTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-button v-if="canManageRules" type="primary" @click="openCreate">新建规则</el-button>
          <el-button v-if="canManageRules" text @click="seedExample">生成示例规则</el-button>
          <template v-if="canManageRules && templates.length">
            <el-select v-model="selectedTemplate" placeholder="行业模板一键套用" style="width: 220px">
              <el-option
                v-for="tpl in templates"
                :key="tpl.key"
                :label="tpl.name"
                :value="tpl.key"
              >
                <span>{{ tpl.name }}</span>
                <span class="tpl-desc">{{ tpl.desc }}</span>
              </el-option>
            </el-select>
            <el-button :disabled="!selectedTemplate" @click="applyTemplate">套用模板</el-button>
          </template>
          <span class="toolbar-count">共 {{ rules.length }} 条规则</span>
        </div>

        <el-table :data="rules" v-loading="loading" row-key="id">
          <el-table-column prop="level" label="级别" width="70" align="center" />
          <el-table-column prop="name" label="规则名称" min-width="150" />
          <el-table-column label="金额区间" width="200">
            <template #default="{ row }">{{ rangeText(row) }}</template>
          </el-table-column>
          <el-table-column label="审批方式" width="180">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.mode === 'counter' ? 'warning' : row.mode === 'or' ? 'success' : 'info'">
                {{ row.mode === 'or' ? '或签' : row.mode === 'counter' ? '会签' : '单人' }}
              </el-tag>
              <span v-if="row.mode === 'or' || row.mode === 'counter'" class="perm">{{ row.approvers }}</span>
            </template>
          </el-table-column>
          <el-table-column label="审批角色" width="200">
            <template #default="{ row }">
              {{ row.level_name || '—' }}
              <span class="perm">{{ row.permission }}</span>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="light">
                {{ row.enabled ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="canManageRules" label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="removeRule(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <p class="empty-text">还没有规则，所有订单都按单级审批。可点「生成示例规则」快速开始。</p>
          </template>
        </el-table>

        <div class="preview-box">
          <span>按条件预览：</span>
          <el-input-number v-model="amountPreview" :min="0" :controls="false" style="width: 130px" @change="loadPreview" />
          <el-select v-model="previewLevel" clearable placeholder="客户等级" style="width: 110px" @change="loadPreview">
            <el-option label="A 级" value="A" />
            <el-option label="B 级" value="B" />
            <el-option label="C 级" value="C" />
          </el-select>
          <el-select v-model="previewWarehouse" clearable placeholder="仓库" style="width: 130px" @change="loadPreview">
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
          <template v-if="preview">
            <el-tag v-for="level in preview.levels" :key="level.level" size="small" effect="plain" class="preview-tag">
              {{ level.level }}. {{ level.name }}
            </el-tag>
            <span class="preview-hint">共 {{ preview.required }} 级</span>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane label="超时规则" name="sla">
        <div class="toolbar">
          <el-button :loading="slaLoading" @click="loadSla"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
          <el-button type="primary" @click="scanSlaNow">立即巡检</el-button>
          <span v-if="slaScanResult" class="toolbar-count">
            上次巡检：待审批 {{ slaScanResult.checked }} 条，提醒 {{ slaScanResult.reminded }} 条，升级 {{ slaScanResult.escalated }} 条
          </span>
        </div>
        <p class="sla-hint">
          待审批超过<b>提醒小时</b>后先提醒审批人；开启<b>自动升级</b>并超过<b>升级小时</b>时，会在审批轨迹里记录「超时升级」并推送上级关注，
          但不会代替审批人签字（财务单据不由系统自动通过）。系统每 15 分钟自动巡检一次。
        </p>
        <el-table :data="slaRows" v-loading="slaLoading" size="small">
          <el-table-column prop="label" label="单据类型" width="160" />
          <el-table-column label="启用" width="90" align="center">
            <template #default="{ row }">
              <el-switch v-model="row.enabled" />
            </template>
          </el-table-column>
          <el-table-column label="提醒(小时)" width="150">
            <template #default="{ row }">
              <el-input-number v-model="row.remind_hours" :min="1" :max="720" :controls="false" style="width: 110px" />
            </template>
          </el-table-column>
          <el-table-column label="自动升级" width="100" align="center">
            <template #default="{ row }">
              <el-switch v-model="row.escalateOn" @change="(val) => toggleEscalate(row, val)" />
            </template>
          </el-table-column>
          <el-table-column label="升级(小时)" min-width="190">
            <template #default="{ row }">
              <el-input-number
                v-if="row.escalateOn"
                v-model="row.escalate_hours"
                :min="1" :max="720" :controls="false"
                style="width: 110px"
              />
              <span v-else class="muted">—（先提醒后升级）</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="140">
            <template #default="{ row }">
              <span v-if="row.configured" class="muted">已配置 · {{ row.updated_by || '—' }}</span>
              <span v-else class="muted">未配置（按默认 24 小时提醒）</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100" align="center">
            <template #default="{ row }">
              <el-button link type="primary" :loading="slaSaving === row.doc_type" @click="saveSla(row)">保存</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="委托代理" name="delegations">
        <div class="toolbar">
          <el-button v-if="canManageRules" type="primary" @click="openDelegationCreate">新增委托</el-button>
          <span class="toolbar-count">共 {{ delegations.length }} 条委托</span>
        </div>
        <el-table :data="delegations" v-loading="delegationLoading" row-key="id" size="small">
          <el-table-column label="单据类型" width="120">
            <template #default="{ row }">{{ docTypeLabel[row.doc_type] || row.doc_type }}</template>
          </el-table-column>
          <el-table-column prop="delegator" label="委托人" width="160" />
          <el-table-column label="" width="40" align="center">
            <template #default>→</template>
          </el-table-column>
          <el-table-column prop="delegatee" label="代理人" width="160" />
          <el-table-column label="启用" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="light">
                {{ row.enabled ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="canManageRules" label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDelegationEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="removeDelegation(row)">删除</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <p class="empty-text">暂无委托关系。委托后可由代理人代表委托人审批对应单据类型。</p>
          </template>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="信用策略" name="credit">
        <el-alert
          v-if="!canManageCredit"
          type="info"
          :closable="false"
          title="当前角色没有信用策略配置权限"
          show-icon
        />
        <template v-else>
          <el-form label-width="120px" class="policy-form">
            <el-form-item label="默认策略">
              <el-select v-model="policies['*']" style="width: 200px">
                <el-option v-for="item in policyOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
            <el-form-item v-for="level in ['A', 'B', 'C']" :key="level" :label="`${level} 级客户`">
              <el-select v-model="policies[level]" style="width: 200px">
                <el-option v-for="item in policyOptions" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveConfig">保存策略</el-button>
            </el-form-item>
          </el-form>

          <h4 class="section-title">超额特批申请</h4>
          <el-table :data="overrides" v-loading="overrideLoading" size="small">
            <el-table-column prop="order_id" label="订单 ID" width="90" />
            <el-table-column label="订单金额" width="120" align="right">
              <template #default="{ row }">¥{{ money(row.amount) }}</template>
            </el-table-column>
            <el-table-column label="超额" width="110" align="right">
              <template #default="{ row }">¥{{ money(row.over_amount) }}</template>
            </el-table-column>
            <el-table-column prop="reason" label="申请原因" min-width="160" show-overflow-tooltip />
            <el-table-column prop="requested_by" label="申请人" width="100" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'approved' ? 'success' : row.status === 'rejected' ? 'danger' : 'warning'">
                  {{ row.status === 'approved' ? '已通过' : row.status === 'rejected' ? '已驳回' : '待审批' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <template v-if="row.status === 'pending'">
                  <el-button link type="success" @click="decide(row, 'approve')">通过</el-button>
                  <el-button link type="danger" @click="decide(row, 'reject')">驳回</el-button>
                </template>
                <span v-else class="empty-text">{{ row.decided_by || '—' }}</span>
              </template>
            </el-table-column>
            <template #empty>
              <p class="empty-text">暂无特批申请</p>
            </template>
          </el-table>
        </template>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑审批规则' : '新建审批规则'" width="520px">
      <el-form label-width="96px">
        <el-form-item label="单据类型">
          <el-select v-model="form.doc_type" style="width: 100%">
            <el-option v-for="item in docTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="规则名称">
          <el-input v-model="form.name" placeholder="如：大额销售二级审批" />
        </el-form-item>
        <el-form-item label="金额下限">
          <el-input-number v-model="form.min_amount" :min="0" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="金额上限">
          <el-input-number
            v-model="form.max_amount"
            :min="0"
            :controls="false"
            placeholder="留空表示不设上限"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="审批级别">
          <el-input-number v-model="form.level" :min="1" :max="5" style="width: 100%" />
        </el-form-item>
        <el-form-item label="审批角色名">
          <el-input v-model="form.level_name" placeholder="如：财务复核" />
        </el-form-item>
        <el-form-item label="所需权限">
          <el-select v-model="form.permission" style="width: 100%">
            <el-option v-for="item in permissionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="审批模式">
          <el-select v-model="form.mode" style="width: 100%">
            <el-option v-for="item in MODE_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.mode === 'or' || form.mode === 'counter'" label="审批人名单">
          <el-input v-model="form.approvers" placeholder="用户名，逗号分隔，如：zhangsan,lisi" />
        </el-form-item>

        <el-divider content-position="left">条件分支（可选，与金额同时满足才命中）</el-divider>
        <el-form-item label="客户等级">
          <el-select
            v-model="conditionForm.levels"
            multiple
            collapse-tags
            placeholder="不选表示不限等级"
            style="width: 100%"
          >
            <el-option label="A 级" value="A" />
            <el-option label="B 级" value="B" />
            <el-option label="C 级" value="C" />
          </el-select>
        </el-form-item>
        <el-form-item label="信用超额">
          <el-switch v-model="conditionForm.creditExceeded" active-text="仅信用超额时命中" />
        </el-form-item>
        <el-form-item label="毛利率低于">
          <el-input-number
            v-model="conditionForm.minMargin"
            :min="0"
            :max="100"
            :precision="1"
            :controls="false"
            placeholder="留空表示不限，如 15 表示低于 15%"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="限定仓库">
          <el-select
            v-model="conditionForm.warehouseIds"
            multiple
            collapse-tags
            placeholder="不选表示不限仓库（仅出库/退货/盘点有仓库）"
            style="width: 100%"
          >
            <el-option v-for="w in warehouses" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="限定制单人">
          <el-input v-model="conditionForm.createdBy" placeholder="用户名，逗号分隔，如：sales01,sales02；留空不限" />
        </el-form-item>
        <el-form-item label="商品分类">
          <el-select
            v-model="conditionForm.categories"
            multiple
            filterable
            allow-create
            default-first-option
            collapse-tags
            placeholder="不选表示不限分类；命中任一分类即生效"
            style="width: 100%"
          >
            <el-option v-for="c in productCategories" :key="c" :label="c" :value="c" />
          </el-select>
        </el-form-item>

        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRule">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="delegationDialog" :title="delegationEditingId ? '编辑委托' : '新增委托'" width="460px">
      <el-form label-width="96px">
        <el-form-item label="单据类型">
          <el-select v-model="delegationForm.doc_type" style="width: 100%">
            <el-option v-for="item in docTypeOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="委托人">
          <el-input v-model="delegationForm.delegator" placeholder="委托人用户名（原审批人）" />
        </el-form-item>
        <el-form-item label="代理人">
          <el-input v-model="delegationForm.delegatee" placeholder="代理人用户名（代其审批）" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="delegationForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="delegationDialog = false">取消</el-button>
        <el-button type="primary" :loading="delegationSaving" @click="saveDelegation">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.perm {
  margin-left: 6px;
  font-size: 11px;
  color: var(--text-muted);
}

.preview-box {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  font-size: 13px;
  color: var(--text-secondary);
}

.tpl-desc {
  margin-left: 10px;
  font-size: 12px;
  color: var(--text-secondary);
}

.preview-tag {
  margin-right: 4px;
}

.sla-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

.preview-hint {
  color: var(--text-muted);
  font-size: 12px;
}

.policy-form {
  max-width: 520px;
}

.section-title {
  margin: 20px 0 10px;
  font-size: 14px;
}
</style>
