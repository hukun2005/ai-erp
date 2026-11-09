<script setup>
import { onMounted, reactive, ref } from 'vue'
import { auditApi } from '../api'

const summary = ref({})
const actions = ref([])
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const query = reactive({ keyword: '', action: '', result: '', role: '' })

const ROLE_NAMES = {
  admin: '管理员',
  sales: '销售员',
  purchaser: '采购员',
  warehouse: '仓管员',
  finance: '财务',
}

const resultMap = {
  success: { text: '成功', type: 'success' },
  failed: { text: '失败', type: 'warning' },
  denied: { text: '已拦截', type: 'danger' },
}

const actionType = (action) => {
  if (action === '越权访问' || action === '未授权访问') return 'danger'
  if (action === '登录失败') return 'warning'
  if (action.startsWith('登录')) return 'success'
  return 'info'
}

async function loadSummary() {
  summary.value = await auditApi.summary()
}

async function load() {
  loading.value = true
  try {
    const data = await auditApi.list({ ...query, page: page.value, page_size: pageSize.value })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

function refresh() {
  loadSummary()
  load()
}

function timeText(value) {
  return value?.replace('T', ' ').slice(0, 19) || ''
}

onMounted(async () => {
  const [act] = await Promise.all([auditApi.actions(), loadSummary(), load()])
  actions.value = act
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">权限审计日志</h1>
        <p class="page-subtitle">记录登录、越权拦截与关键业务操作，用于权限合规追溯</p>
      </div>
      <el-button @click="refresh"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
    </div>

    <div class="metric-grid">
      <div class="metric-card">
        <span>今日操作总数</span>
        <strong>{{ summary.today_total ?? 0 }}</strong>
        <p>累计 {{ summary.total ?? 0 }} 条审计记录</p>
      </div>
      <div class="metric-card danger">
        <span>今日越权拦截</span>
        <strong>{{ summary.denied_today ?? 0 }}</strong>
        <p>401 / 403 自动记录</p>
      </div>
      <div class="metric-card warning">
        <span>今日登录失败</span>
        <strong>{{ summary.login_failed_today ?? 0 }}</strong>
        <p>账号或密码错误</p>
      </div>
      <div class="metric-card">
        <span>今日权限变更</span>
        <strong>{{ summary.user_changes_today ?? 0 }}</strong>
        <p>用户增删改与密码重置</p>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索用户 / 动作 / 资源 / 详情"
          clearable
          style="width: 250px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="query.action" placeholder="全部动作" clearable style="width: 160px" @change="search">
          <el-option v-for="a in actions" :key="a" :label="a" :value="a" />
        </el-select>
        <el-select v-model="query.result" placeholder="全部结果" clearable style="width: 130px" @change="search">
          <el-option v-for="(v, k) in resultMap" :key="k" :label="v.text" :value="k" />
        </el-select>
        <el-select v-model="query.role" placeholder="全部角色" clearable style="width: 130px" @change="search">
          <el-option v-for="(name, key) in ROLE_NAMES" :key="key" :label="name" :value="key" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column label="时间" width="165">
          <template #default="{ row }">{{ timeText(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="username" label="操作人" width="110" />
        <el-table-column prop="role_name" label="角色" width="90" />
        <el-table-column label="动作" width="150">
          <template #default="{ row }">
            <el-tag :type="actionType(row.action)" effect="plain" size="small">{{ row.action }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="resultMap[row.result]?.type || 'info'" effect="light" size="small">
              {{ resultMap[row.result]?.text || row.result }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="resource" label="资源" min-width="180" show-overflow-tooltip />
        <el-table-column prop="detail" label="详情" min-width="220" show-overflow-tooltip />
        <el-table-column label="来源" width="210">
          <template #default="{ row }">
            <div class="source">
              <span class="ip">{{ row.ip || '-' }}</span>
              <span class="path">{{ row.method }} {{ row.path }}</span>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}

.metric-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
}

.metric-card span {
  font-size: 12px;
  color: var(--text-secondary);
}

.metric-card strong {
  display: block;
  font-size: 22px;
  margin: 6px 0 2px;
}

.metric-card p {
  margin: 0;
  font-size: 12px;
  color: #9ca3af;
}

.metric-card.danger strong {
  color: var(--danger);
}

.metric-card.warning strong {
  color: var(--warning);
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.source {
  display: flex;
  flex-direction: column;
  font-size: 12px;
  color: var(--text-secondary);
}

.source .path {
  font-family: monospace;
  font-size: 11px;
  color: #9ca3af;
}
</style>
