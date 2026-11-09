<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { automationApi } from '../api'

const loading = ref(false)
const rules = ref([])
const options = ref({ triggers: [], actions: [] })
const saving = ref(false)

const dialogVisible = ref(false)
const editingId = ref(null)
const formRef = ref()

const form = reactive({
  name: '',
  enabled: true,
  trigger_type: '',
  trigger_config: {},
  action_type: '',
  action_config: {},
  cooldown_minutes: 240,
})

const triggerDef = computed(() => options.value.triggers.find((item) => item.value === form.trigger_type))
const actionDef = computed(() => options.value.actions.find((item) => item.value === form.action_type))

const resultVisible = ref(false)
const resultTitle = ref('')
const result = ref(null)

const logsVisible = ref(false)
const logsTitle = ref('')
const logs = ref([])

const runStatusMap = {
  success: { text: '成功', type: 'success' },
  skipped: { text: '已跳过', type: 'info' },
  failed: { text: '失败', type: 'danger' },
  dry_run: { text: '试运行', type: 'warning' },
}

function money(value) {
  return '¥' + Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })
}

/** 切换触发/动作时补齐默认参数，避免提交空配置 */
function withDefaults(def, source) {
  const next = { ...(source || {}) }
  for (const field of def?.fields || []) {
    const value = next[field.key]
    if (value === undefined || value === null || value === '') {
      next[field.key] = field.default ?? (field.type === 'int' ? 0 : '')
    }
  }
  return next
}

function configText(config) {
  const entries = Object.entries(config || {})
  if (!entries.length) return ''
  return entries.map(([key, value]) => `${key}=${value}`).join('，')
}

async function load() {
  loading.value = true
  try {
    rules.value = await automationApi.list()
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  options.value = await automationApi.options()
}

watch(
  () => form.trigger_type,
  () => {
    form.trigger_config = withDefaults(triggerDef.value, form.trigger_config)
  }
)

watch(
  () => form.action_type,
  () => {
    form.action_config = withDefaults(actionDef.value, form.action_config)
  }
)

function openCreate() {
  editingId.value = null
  form.name = ''
  form.enabled = true
  form.cooldown_minutes = 240
  form.trigger_type = options.value.triggers[0]?.value || ''
  form.trigger_config = {}
  form.action_type = options.value.actions[0]?.value || ''
  form.action_config = {}
  form.trigger_config = withDefaults(triggerDef.value, {})
  form.action_config = withDefaults(actionDef.value, {})
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.name = row.name
  form.enabled = row.enabled
  form.cooldown_minutes = row.cooldown_minutes
  form.trigger_type = row.trigger_type
  form.trigger_config = withDefaults(triggerDef.value, row.trigger_config)
  form.action_type = row.action_type
  form.action_config = withDefaults(actionDef.value, row.action_config)
  dialogVisible.value = true
}

async function submit() {
  await formRef.value.validate()
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      enabled: form.enabled,
      trigger_type: form.trigger_type,
      trigger_config: form.trigger_config,
      action_type: form.action_type,
      action_config: form.action_config,
      cooldown_minutes: form.cooldown_minutes,
    }
    if (editingId.value) {
      await automationApi.update(editingId.value, payload)
      ElMessage.success('规则已更新')
    } else {
      await automationApi.create(payload)
      ElMessage.success('规则已创建，可先试运行确认命中对象')
    }
    dialogVisible.value = false
    load()
  } finally {
    saving.value = false
  }
}

async function toggle(row) {
  try {
    await automationApi.update(row.id, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '规则已启用，调度器会按周期执行' : '规则已停用')
  } catch (err) {
    row.enabled = !row.enabled
  }
}

function showResult(title, data) {
  result.value = data
  resultTitle.value = title
  resultVisible.value = true
}

async function dryRun(row) {
  const data = await automationApi.run(row.id, true)
  showResult(`试运行 · ${row.name}`, data)
}

async function execute(row) {
  await ElMessageBox.confirm(
    `立即执行「${row.name}」？动作只会生成草稿单或发送通知，不会改动库存与账务。`,
    '执行确认',
    { type: 'warning' }
  )
  const data = await automationApi.run(row.id, false)
  showResult(`执行结果 · ${row.name}`, data)
  ElMessage[data.status === 'failed' ? 'error' : 'success'](data.summary)
  load()
}

async function showLogs(row) {
  logs.value = await automationApi.runs(row.id)
  logsTitle.value = `执行记录 · ${row.name}`
  logsVisible.value = true
}

async function showRecentLogs() {
  logs.value = await automationApi.recentRuns(50)
  logsTitle.value = '最近执行记录'
  logsVisible.value = true
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除规则「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await automationApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(async () => {
  await loadOptions()
  await load()
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">自动化规则</h1>
        <p class="page-subtitle">
          配好「什么条件下做什么」，由后台按周期自动执行；动作只生成草稿单或发通知，不会擅自改动账实
        </p>
      </div>
      <div class="header-actions">
        <el-button @click="showRecentLogs"><el-icon><Tickets /></el-icon>&nbsp;执行记录</el-button>
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建规则</el-button>
      </div>
    </div>

    <div class="card">
      <el-table :data="rules" v-loading="loading" empty-text="还没有规则，先建一条低库存自动补货试试">
        <el-table-column label="规则名称" min-width="180">
          <template #default="{ row }">
            <div class="rule-name">{{ row.name }}</div>
            <div class="rule-sub">{{ row.trigger_description || row.trigger_label }}</div>
          </template>
        </el-table-column>
        <el-table-column label="执行动作" min-width="200">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" type="primary">{{ row.action_label }}</el-tag>
            <div v-if="configText(row.action_config)" class="rule-sub">{{ configText(row.action_config) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="冷却" width="100" align="center">
          <template #default="{ row }">{{ row.cooldown_minutes }} 分钟</template>
        </el-table-column>
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="toggle(row)" />
          </template>
        </el-table-column>
        <el-table-column label="上次执行" width="180">
          <template #default="{ row }">
            <span v-if="row.last_run_at">
              {{ String(row.last_run_at).replace('T', ' ').slice(0, 16) }}
              <el-tag :type="runStatusMap[row.last_status]?.type" size="small" effect="light">
                {{ runStatusMap[row.last_status]?.text || row.last_status }}
              </el-tag>
            </span>
            <span v-else class="muted">从未执行</span>
          </template>
        </el-table-column>
        <el-table-column prop="run_count" label="执行次数" width="90" align="center" />
        <el-table-column label="操作" width="290" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="dryRun(row)">试运行</el-button>
            <el-button link type="success" @click="execute(row)">立即执行</el-button>
            <el-button link type="info" @click="showLogs(row)">记录</el-button>
            <el-button link type="info" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 新建 / 编辑 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑规则' : '新建规则'" width="680px" top="8vh">
      <el-form ref="formRef" :model="form" label-width="100px">
        <el-form-item
          label="规则名称"
          prop="name"
          :rules="[{ required: true, message: '请输入规则名称', trigger: 'blur' }]"
        >
          <el-input v-model="form.name" maxlength="80" show-word-limit placeholder="例如：低库存自动生成采购草稿" />
        </el-form-item>

        <el-form-item label="触发条件">
          <el-select v-model="form.trigger_type" style="width: 100%">
            <el-option v-for="item in options.triggers" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <p class="field-help">{{ triggerDef?.description }}</p>
        </el-form-item>

        <el-form-item v-for="field in triggerDef?.fields || []" :key="'t-' + field.key" :label="field.label">
          <el-input-number
            v-if="field.type === 'int'"
            v-model="form.trigger_config[field.key]"
            :min="field.min"
            :max="field.max"
            style="width: 200px"
          />
          <el-input v-else v-model="form.trigger_config[field.key]" style="width: 100%" />
        </el-form-item>

        <el-form-item label="执行动作">
          <el-select v-model="form.action_type" style="width: 100%">
            <el-option v-for="item in options.actions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <p class="field-help">{{ actionDef?.description }}</p>
        </el-form-item>

        <el-form-item v-for="field in actionDef?.fields || []" :key="'a-' + field.key" :label="field.label">
          <el-input-number
            v-if="field.type === 'int'"
            v-model="form.action_config[field.key]"
            :min="field.min"
            :max="field.max"
            style="width: 200px"
          />
          <el-input v-else v-model="form.action_config[field.key]" style="width: 100%" />
        </el-form-item>

        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="冷却时间">
              <el-input-number v-model="form.cooldown_minutes" :min="0" :max="10080" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="启用">
              <el-switch v-model="form.enabled" />
              <span class="field-inline">启用后按周期自动执行</span>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 试运行 / 执行结果 -->
    <el-drawer v-model="resultVisible" :title="resultTitle" size="560px">
      <div v-if="result" class="result">
        <div class="result-head">
          <el-tag :type="runStatusMap[result.status]?.type" effect="light">
            {{ runStatusMap[result.status]?.text || result.status }}
          </el-tag>
          <span class="result-summary">{{ result.summary }}</span>
        </div>

        <template v-if="result.payload?.created_orders?.length">
          <h4>已生成的采购草稿</h4>
          <el-table :data="result.payload.created_orders" size="small">
            <el-table-column prop="order_no" label="单号" width="150" />
            <el-table-column prop="supplier_name" label="供应商" min-width="120" show-overflow-tooltip />
            <el-table-column prop="item_count" label="商品数" width="80" align="center" />
            <el-table-column label="金额" width="110" align="right">
              <template #default="{ row }">{{ money(row.amount) }}</template>
            </el-table-column>
          </el-table>
        </template>

        <template v-if="result.payload?.preview?.orders?.length">
          <h4>试运行预览（未写入）</h4>
          <div v-for="order in result.payload.preview.orders" :key="order.supplier_id" class="preview-order">
            <div class="preview-head">
              <strong>{{ order.supplier_name || '未命名供应商' }}</strong>
              <span>{{ order.item_count }} 个商品 · {{ money(order.amount) }}</span>
            </div>
            <div v-for="item in order.items" :key="item.product_id" class="preview-item">
              <span>{{ item.sku }} {{ item.name }}</span>
              <span>建议补 {{ item.quantity }} · {{ money(item.amount) }}</span>
            </div>
          </div>
        </template>

        <template v-if="result.payload?.skipped?.length">
          <h4>跳过</h4>
          <ul class="skipped">
            <li v-for="(reason, index) in result.payload.skipped" :key="index">{{ reason }}</li>
          </ul>
        </template>

        <h4>命中对象（{{ result.hit_count }}）</h4>
        <div v-if="result.hits?.length" class="hits">
          <div v-for="(hit, index) in result.hits" :key="index" class="hit">{{ hit.label || hit.name }}</div>
        </div>
        <p v-else class="muted">没有命中对象</p>
      </div>
    </el-drawer>

    <!-- 执行记录 -->
    <el-drawer v-model="logsVisible" :title="logsTitle" size="520px">
      <el-timeline v-if="logs.length">
        <el-timeline-item
          v-for="log in logs"
          :key="log.id"
          :timestamp="String(log.created_at).replace('T', ' ').slice(0, 16)"
          :type="runStatusMap[log.status]?.type === 'danger' ? 'danger' : runStatusMap[log.status]?.type === 'success' ? 'success' : 'primary'"
        >
          <div class="log-head">
            <el-tag :type="runStatusMap[log.status]?.type" size="small" effect="light">
              {{ runStatusMap[log.status]?.text || log.status }}
            </el-tag>
            <span>{{ log.rule_name }}</span>
          </div>
          <p class="log-summary">{{ log.summary }}</p>
          <details v-if="log.detail" class="log-detail">
            <summary>命中明细</summary>
            <pre>{{ log.detail }}</pre>
          </details>
        </el-timeline-item>
      </el-timeline>
      <p v-else class="empty-text">还没有执行记录，点一次「试运行」或「立即执行」就会出现</p>
    </el-drawer>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 10px;
}

.rule-name {
  font-weight: 500;
}

.rule-sub {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.muted {
  color: var(--text-secondary);
  font-size: 12px;
}

.field-help {
  width: 100%;
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.field-inline {
  margin-left: 10px;
  font-size: 12px;
  color: var(--text-secondary);
}

.result-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.result-summary {
  font-size: 14px;
}

.result h4 {
  margin: 18px 0 10px;
  font-size: 14px;
}

.preview-order {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 10px;
}

.preview-head {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 8px;
}

.preview-item {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-secondary);
  padding: 3px 0;
}

.skipped {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--text-secondary);
}

.hits {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.hit {
  font-size: 13px;
  padding: 6px 10px;
  background: #f8fafc;
  border-radius: 6px;
}

.log-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.log-summary {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.log-detail {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}

.log-detail pre {
  white-space: pre-wrap;
  margin: 6px 0 0;
  font-family: inherit;
}
</style>
