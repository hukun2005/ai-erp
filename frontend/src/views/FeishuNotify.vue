<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { notificationApi } from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()

const status = ref({
  configured: false,
  webhook: '',
  secret_configured: false,
  enabled: false,
  source: 'none',
  kinds: [],
})
const config = ref({
  configured: false,
  enabled: false,
  source: 'none',
  webhook_url: '',
  webhook: '',
  secret_configured: false,
  updated_by: '',
  updated_at: '',
})

const form = reactive({ webhook_url: '', secret: '', enabled: true })

const alertRule = ref({
  enabled: false,
  condition: 'below_safety',
  condition_label: '低于安全库存',
  cooldown_minutes: 120,
  include_replenishment: true,
  pending_count: 0,
  last_triggered_at: '',
  updated_by: '',
  feishu_ready: false,
})
const ruleForm = reactive({
  enabled: false,
  condition: 'below_safety',
  cooldown_minutes: 120,
  include_replenishment: true,
})
const ruleSaving = ref(false)

const loading = ref(false)
const saving = ref(false)
const clearing = ref(false)
const previewing = ref('')
const sending = ref('')
const testing = ref(false)
const dialogVisible = ref(false)
const preview = ref(null)

const previewLines = computed(() => {
  const element = preview.value?.card?.elements?.[0]
  const content = element?.text?.content || ''
  return content.split('\n').filter((line) => line.trim())
})

const canSend = computed(() => status.value.configured)
const hasSaved = computed(() => config.value.source === 'database')
const hasInput = computed(() => form.webhook_url.trim().length > 0)
const secretPlaceholder = computed(() =>
  config.value.secret_configured ? '已保存密钥，留空表示保持不变' : '开启「签名校验」时填写，未开启可留空'
)

function fmtTime(value) {
  return value ? value.replace('T', ' ').slice(0, 16) : ''
}

async function loadStatus() {
  loading.value = true
  try {
    const [s, c, r] = await Promise.all([
      notificationApi.status(),
      notificationApi.config(),
      notificationApi.alertRule(),
    ])
    status.value = s
    config.value = c
    form.webhook_url = c.webhook_url || ''
    form.secret = ''
    form.enabled = c.source === 'none' ? true : !!c.enabled
    alertRule.value = r
    Object.assign(ruleForm, {
      enabled: !!r.enabled,
      condition: r.condition || 'below_safety',
      cooldown_minutes: r.cooldown_minutes || 120,
      include_replenishment: !!r.include_replenishment,
    })
  } finally {
    loading.value = false
  }
}

function validateForm() {
  const url = form.webhook_url.trim()
  if (!url) {
    ElMessage.warning('请填写飞书机器人 Webhook 地址')
    return ''
  }
  if (!/^https?:\/\//.test(url)) {
    ElMessage.warning('Webhook 地址必须以 http:// 或 https:// 开头')
    return ''
  }
  if (!url.includes('open-apis/bot')) {
    ElMessage.warning('地址格式不正确，应为 https://open.feishu.cn/open-apis/bot/v2/hook/...')
    return ''
  }
  if (form.secret.trim().length > 200) {
    ElMessage.warning('签名密钥过长，最多 200 个字符')
    return ''
  }
  return url
}

async function saveConfig() {
  const url = validateForm()
  if (!url) return
  const secret = form.secret.trim()
  saving.value = true
  try {
    // 留空且已保存过密钥时传 null，表示保持原密钥不变
    const payloadSecret = secret ? secret : config.value.secret_configured ? null : ''
    const data = await notificationApi.saveConfig({
      webhook_url: url,
      secret: payloadSecret,
      enabled: form.enabled,
    })
    ElMessage.success(data.message || '配置已保存')
    await loadStatus()
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  const url = validateForm()
  if (!url) return
  testing.value = true
  try {
    // 未保存的新地址直接用当前输入测试，已保存地址则用服务端配置
    if (url === (config.value.webhook_url || '').trim()) {
      await notificationApi.test()
    } else {
      await notificationApi.testConfig({ webhook_url: url, secret: form.secret.trim() })
    }
    ElMessage.success('测试消息已发送，请到飞书群确认')
  } finally {
    testing.value = false
  }
}

async function clearConfig() {
  try {
    await ElMessageBox.confirm('清除后需要重新填写 Webhook 才能推送消息，确定清除吗？', '清除配置', {
      type: 'warning',
    })
  } catch {
    return
  }
  clearing.value = true
  try {
    await notificationApi.clearConfig()
    ElMessage.success('已清除飞书机器人配置')
    form.webhook_url = ''
    form.secret = ''
    form.enabled = true
    await loadStatus()
  } finally {
    clearing.value = false
  }
}

async function saveAlertRule() {
  if (ruleForm.cooldown_minutes < 1 || ruleForm.cooldown_minutes > 1440) {
    ElMessage.warning('冷却时间需在 1~1440 分钟之间')
    return
  }
  if (ruleForm.enabled && !canSend.value) {
    ElMessage.warning('请先在「飞书机器人接入」中保存并启用 Webhook，否则无法自动推送')
    return
  }
  ruleSaving.value = true
  try {
    await notificationApi.saveAlertRule({
      enabled: ruleForm.enabled,
      condition: ruleForm.condition,
      cooldown_minutes: ruleForm.cooldown_minutes,
      include_replenishment: ruleForm.include_replenishment,
    })
    ElMessage.success('自动预警规则已保存')
    await loadStatus()
  } finally {
    ruleSaving.value = false
  }
}

async function openPreview(kind) {
  previewing.value = kind
  try {
    preview.value = await notificationApi.preview({ kind })
    dialogVisible.value = true
  } finally {
    previewing.value = ''
  }
}

async function sendMessage(kind, name, dryRun = false) {
  if (!dryRun) {
    try {
      await ElMessageBox.confirm(
        `将把「${name}」推送到飞书管理层群，确认发送吗？`,
        '发送确认',
        { type: 'warning' }
      )
    } catch {
      return
    }
  }
  sending.value = kind
  try {
    const data = await notificationApi.send({ kind, dry_run: dryRun })
    if (dryRun) {
      ElMessage.success(`模拟发送成功：${data.summary}`)
    } else {
      ElMessage.success(`已发送到飞书群：${data.summary}`)
    }
  } finally {
    sending.value = ''
  }
}

onMounted(loadStatus)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">飞书通知</h1>
        <p class="page-subtitle">
          把库存预警、待决策订单与经营日报推送到管理层飞书群（{{ userStore.roleName }}视角）
        </p>
      </div>
      <el-button :loading="loading" @click="loadStatus">
        <el-icon><Refresh /></el-icon>&nbsp;刷新状态
      </el-button>
    </div>

    <div class="card">
      <div class="panel-head">
        <h3>飞书机器人接入</h3>
        <span class="hint">填写后保存即时生效，无需修改代码或重启服务</span>
      </div>

      <el-form class="config-form" label-width="100px" @submit.prevent>
        <el-form-item label="Webhook 地址">
          <el-input
            v-model="form.webhook_url"
            clearable
            maxlength="300"
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx"
          />
        </el-form-item>
        <el-form-item label="签名密钥">
          <el-input
            v-model="form.secret"
            type="password"
            show-password
            maxlength="200"
            :placeholder="secretPlaceholder"
          />
          <div class="field-tip">
            密钥保存在服务端数据库、页面不回显；只有机器人开启「签名校验」时才需要填写。
          </div>
        </el-form-item>
        <el-form-item label="启用推送">
          <el-switch v-model="form.enabled" active-text="启用" inactive-text="停用" />
          <span class="field-tip">停用后「发送到飞书」按钮不可用，仍可预览与模拟发送。</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveConfig">保存配置</el-button>
          <el-button :loading="testing" :disabled="!hasInput" @click="testConnection">测试连接</el-button>
          <el-button type="danger" plain :loading="clearing" :disabled="!hasSaved" @click="clearConfig">
            清除配置
          </el-button>
        </el-form-item>
      </el-form>

      <div class="status-row">
        <el-tag :type="canSend ? 'success' : 'info'" effect="dark">
          {{ canSend ? '已接入' : '未接入' }}
        </el-tag>
        <span class="masked">当前地址：{{ status.webhook || '未填写' }}</span>
        <span class="masked">签名校验：{{ status.secret_configured ? '已开启' : '未开启' }}</span>
        <span v-if="hasSaved" class="masked">
          最近更新：{{ config.updated_by || '-' }} · {{ fmtTime(config.updated_at) || '-' }}
        </span>
        <span v-if="status.source === 'env'" class="masked">来源：.env 环境变量</span>
      </div>

      <div class="config-tip">
        <el-icon><InfoFilled /></el-icon>
        <div>
          <strong>获取地址：</strong>飞书群 → 右上角「设置」→「群机器人」→「添加自定义机器人」→
          复制生成的 Webhook 粘贴到上方；若勾选「签名校验」，把显示的密钥填入「签名密钥」。
          点「测试连接」发送一条测试卡片，群内收到即表示接入成功。
        </div>
      </div>
    </div>

    <div class="card">
      <div class="panel-head">
        <h3>库存自动预警</h3>
        <span class="hint">出库、盘点调减、销售出库或撤销入库后自动检查，跌破条件即推送飞书</span>
      </div>

      <el-form class="config-form" label-width="100px" @submit.prevent>
        <el-form-item label="自动推送">
          <el-switch v-model="ruleForm.enabled" active-text="启用" inactive-text="停用" />
          <span class="field-tip">需先在上方完成飞书接入，否则只检查、不推送</span>
        </el-form-item>
        <el-form-item label="触发条件">
          <el-radio-group v-model="ruleForm.condition">
            <el-radio-button value="below_safety">低于或等于安全库存</el-radio-button>
            <el-radio-button value="out_of_stock">已缺货（库存为 0）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="冷却时间">
          <el-input-number v-model="ruleForm.cooldown_minutes" :min="1" :max="1440" :step="30" />
          <span class="field-tip">同一商品在冷却期内不重复提醒，避免连续出库刷屏</span>
        </el-form-item>
        <el-form-item label="补货建议">
          <el-switch v-model="ruleForm.include_replenishment" active-text="附带合计补货缺口" inactive-text="不附带" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="ruleSaving" @click="saveAlertRule">保存规则</el-button>
        </el-form-item>
      </el-form>

      <div class="status-row">
        <el-tag :type="alertRule.enabled ? 'success' : 'info'" effect="dark">
          {{ alertRule.enabled ? '已启用' : '未启用' }}
        </el-tag>
        <span class="masked">当前符合条件商品：{{ alertRule.pending_count }} 个</span>
        <span class="masked">最近触发：{{ fmtTime(alertRule.last_triggered_at) || '暂无' }}</span>
        <span v-if="alertRule.updated_by" class="masked">规则更新：{{ alertRule.updated_by }}</span>
        <span v-if="alertRule.enabled && !alertRule.feishu_ready" class="masked warn">
          飞书未接入，规则只检查不推送
        </span>
      </div>
    </div>

    <div class="kind-grid">
      <div v-for="kind in status.kinds" :key="kind.key" class="card kind-card">
        <div class="kind-head">
          <h3>{{ kind.name }}</h3>
          <el-tag size="small" effect="plain">{{ kind.key }}</el-tag>
        </div>
        <p class="kind-desc">{{ kind.desc }}</p>
        <div class="kind-actions">
          <el-button size="small" :loading="previewing === kind.key" @click="openPreview(kind.key)">
            预览内容
          </el-button>
          <el-button
            size="small"
            :loading="sending === kind.key"
            @click="sendMessage(kind.key, kind.name, true)"
          >
            模拟发送
          </el-button>
          <el-button
            size="small"
            type="primary"
            :disabled="!canSend"
            :loading="sending === kind.key"
            @click="sendMessage(kind.key, kind.name)"
          >
            发送到飞书
          </el-button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="panel-head">
        <h3>推送说明</h3>
        <span class="hint">消息以飞书交互卡片发送，标题带【AI-ERP】前缀，便于管理层识别</span>
      </div>
      <ul class="tips">
        <li>库存预警：缺货与低于安全库存商品清单，附带库存总价值与补货建议，适合每日早会前推送。</li>
        <li>待决策事项：待确认销售单、待入库采购单、待发货销售单的数量与金额 TOP3，便于管理层审批。</li>
        <li>经营日报：当日销售额、订单数、热销 TOP3 与库存健康度，适合下班前汇报。</li>
        <li>每次保存配置、测试连接与真实发送都会写入审计日志，失败原因一并记录。</li>
      </ul>
    </div>

    <el-dialog v-model="dialogVisible" :title="`${preview?.name || ''} 推送预览`" width="560px">
      <div v-if="preview" class="preview">
        <div class="preview-head">
          <span class="preview-tag">【AI-ERP】{{ preview.name }}</span>
        </div>
        <p class="preview-summary">{{ preview.summary }}</p>
        <div class="preview-body">
          <p v-for="(line, index) in previewLines" :key="index" class="preview-line">
            {{ line.replaceAll('**', '') }}
          </p>
        </div>
      </div>
      <template #footer>
        <el-button @click="dialogVisible = false">关闭</el-button>
        <el-button
          type="primary"
          :disabled="!canSend"
          @click="dialogVisible = false; sendMessage(preview.kind, preview.name)"
        >
          发送到飞书
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.config-form {
  max-width: 720px;
}

.config-form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.field-tip {
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.6;
  margin-left: 10px;
}

.status-row {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.masked {
  font-size: 12px;
  color: #6b7280;
  background: #f3f4f6;
  padding: 4px 10px;
  border-radius: 6px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.masked.warn {
  color: #b45309;
  background: #fef3c7;
}

.config-tip {
  margin-top: 14px;
  display: flex;
  gap: 8px;
  padding: 12px;
  border-radius: 8px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  color: #92400e;
  font-size: 12px;
  line-height: 1.7;
}

.kind-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 16px;
  margin: 16px 0;
}

.kind-card {
  margin: 0;
}

.kind-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kind-head h3 {
  margin: 0;
  font-size: 15px;
}

.kind-desc {
  color: #6b7280;
  font-size: 12px;
  line-height: 1.7;
  min-height: 42px;
  margin: 10px 0 14px;
}

.kind-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tips {
  margin: 0;
  padding-left: 18px;
  color: #4b5563;
  font-size: 13px;
  line-height: 2;
}

.preview-head {
  margin-bottom: 8px;
}

.preview-tag {
  display: inline-block;
  background: #eef2ff;
  color: #4338ca;
  font-weight: 600;
  font-size: 13px;
  padding: 4px 10px;
  border-radius: 6px;
}

.preview-summary {
  color: #6b7280;
  font-size: 12px;
  margin: 0 0 12px;
}

.preview-body {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px 14px;
  max-height: 320px;
  overflow-y: auto;
}

.preview-line {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.7;
  color: #111827;
  white-space: pre-wrap;
}
</style>
