<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { userApi } from '../api'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()
const loading = ref(false)
const roles = ref([])
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const query = reactive({ keyword: '', role: '' })

const dialogVisible = ref(false)
const editing = ref(false)
const formRef = ref()
const form = reactive({ id: null, username: '', password: '', full_name: '', role: 'sales', is_active: true })

const pwdVisible = ref(false)
const pwdForm = reactive({ id: null, username: '', password: '' })

const staffVisible = ref(false)
const staffFormRef = ref()
const staffSending = ref(false)
const staffSubmitting = ref(false)
const staffCountdown = ref(0)
const staffDevCode = ref('')
const staffForm = reactive({
  phone: '',
  code: '',
  username: '',
  full_name: '',
  password: '',
  role: 'sales',
})
let staffTimer = null

const staffRules = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的 11 位手机号', trigger: 'blur' },
  ],
  code: [
    { required: true, message: '请输入验证码', trigger: 'blur' },
    { pattern: /^\d{6}$/, message: '验证码为 6 位数字', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度需为 6~64 位', trigger: 'blur' },
  ],
}

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  full_name: [{ required: true, message: '请输入姓名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
  ],
}

const PERM_LABELS = {
  '*': '全部权限',
  'dashboard:view': '经营看板',
  'product:view': '查看商品',
  'product:manage': '维护商品',
  'inventory:view': '查看库存',
  'inventory:adjust': '出入库/盘点',
  'transfer:view': '查看调拨单',
  'transfer:manage': '创建/执行调拨',
  'warehouse:manage': '仓库与库位维护',
  'customer:view': '查看客户',
  'customer:manage': '维护客户',
  'supplier:view': '查看供应商',
  'supplier:manage': '维护供应商',
  'sales:view': '查看销售订单',
  'sales:manage': '创建/提交销售单',
  'sales:approve': '审批销售订单',
  'purchase:view': '查看采购订单',
  'purchase:manage': '创建/提交采购单',
  'purchase:receive': '采购入库确认',
  'purchase:approve': '审批采购订单',
  'return:view': '查看退货单',
  'return:manage': '创建/确认退货',
  'stocktake:view': '查看盘点单',
  'stocktake:manage': '创建/盘点调整',
  'inbound:view': '查看入库单',
  'inbound:manage': '入库单/照片识别',
  'outbound:view': '查看销售出库',
  'outbound:manage': '发货/签收/回库',
  'approval:manage': '审批规则配置',
  'credit:manage': '信用策略与特批',
  'template:view': '查看单据模板',
  'template:manage': '配置单据模板',
  'finance:view': '财务数据',
  'finance:manage': '财务操作',
  'payable:view': '查看应付账款',
  'payable:manage': '付款与对账',
  'ai:assist': 'AI 问答',
  'ai:insight': 'AI 经营洞察',
  'ai:replenish': 'AI 智能补货',
  'ai:forecast': 'AI 销售预测',
  'user:manage': '用户与角色管理',
  'audit:view': '权限审计日志',
  'notify:manage': '飞书通知推送',
}

const roleMap = computed(() => Object.fromEntries(roles.value.map((r) => [r.role, r])))

async function loadRoles() {
  roles.value = await userApi.roles()
}

async function loadUsers() {
  loading.value = true
  try {
    const data = await userApi.list({ ...query, page: page.value, page_size: pageSize.value })
    list.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  loadUsers()
}

function openCreate() {
  editing.value = false
  Object.assign(form, {
    id: null,
    username: '',
    password: '',
    full_name: '',
    role: roles.value[1]?.role || 'sales',
    is_active: true,
  })
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = true
  Object.assign(form, {
    id: row.id,
    username: row.username,
    password: '',
    full_name: row.full_name,
    role: row.role,
    is_active: row.is_active,
  })
  dialogVisible.value = true
}

async function submit() {
  await formRef.value.validate()
  if (editing.value) {
    await userApi.update(form.id, { full_name: form.full_name, role: form.role, is_active: form.is_active })
    ElMessage.success('用户信息已更新')
  } else {
    await userApi.create({
      username: form.username,
      password: form.password,
      full_name: form.full_name,
      role: form.role,
    })
    ElMessage.success('用户已创建')
  }
  dialogVisible.value = false
  loadUsers()
}

function openReset(row) {
  Object.assign(pwdForm, { id: row.id, username: row.username, password: '' })
  pwdVisible.value = true
}

async function submitReset() {
  if (pwdForm.password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  await userApi.resetPassword(pwdForm.id, pwdForm.password)
  ElMessage.success('密码已重置')
  pwdVisible.value = false
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除用户「${row.username}」吗？`, '删除确认', { type: 'warning' })
  await userApi.remove(row.id)
  ElMessage.success('已删除')
  loadUsers()
}

function openStaff() {
  Object.assign(staffForm, {
    phone: '',
    code: '',
    username: '',
    full_name: '',
    password: '',
    role: roles.value[1]?.role || 'sales',
  })
  staffDevCode.value = ''
  staffCountdown.value = 0
  clearInterval(staffTimer)
  staffVisible.value = true
}

async function sendStaffCode() {
  try {
    await staffFormRef.value.validateField('phone')
  } catch {
    return
  }
  staffSending.value = true
  try {
    const data = await userApi.sendCode(staffForm.phone)
    ElMessage.success(data.message)
    if (data.dev_code) {
      staffDevCode.value = data.dev_code
      staffForm.code = data.dev_code
    }
    staffCountdown.value = 60
    clearInterval(staffTimer)
    staffTimer = setInterval(() => {
      staffCountdown.value -= 1
      if (staffCountdown.value <= 0) clearInterval(staffTimer)
    }, 1000)
  } catch {
    // 错误提示由拦截器统一处理
  } finally {
    staffSending.value = false
  }
}

async function submitStaff() {
  try {
    await staffFormRef.value.validate()
  } catch {
    return
  }
  staffSubmitting.value = true
  try {
    const user = await userApi.registerByPhone({ ...staffForm })
    ElMessage.success(`已开通账号「${user.username}」，可直接登录使用`)
    staffVisible.value = false
    loadUsers()
  } catch {
    // 错误提示由拦截器统一处理
  } finally {
    staffSubmitting.value = false
  }
}

function permText(perm) {
  return PERM_LABELS[perm] || perm
}

onMounted(async () => {
  await loadRoles()
  loadUsers()
})

onBeforeUnmount(() => clearInterval(staffTimer))
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">用户与角色</h1>
        <p class="page-subtitle">分配角色即控制模块权限与 AI 能力；手机号开户需短信验证码，新建用户默认启用</p>
      </div>
      <div class="header-actions">
        <el-button @click="openStaff"><el-icon><Iphone /></el-icon>&nbsp;手机号开户</el-button>
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建用户</el-button>
      </div>
    </div>

    <div class="role-grid">
      <div v-for="role in roles" :key="role.role" class="role-card">
        <div class="role-head">
          <span class="role-dot" :style="{ background: role.color }"></span>
          <strong>{{ role.name }}</strong>
          <el-tag size="small" effect="plain">{{ list.filter((u) => u.role === role.role).length }} 人</el-tag>
          <el-tag size="small" :type="role.data_scope === 'own' ? 'warning' : 'info'" effect="plain">
            {{ role.data_scope_name }}
          </el-tag>
        </div>
        <p class="role-desc">{{ role.description }}</p>
        <div class="perm-list">
          <el-tag v-for="perm in role.permissions" :key="perm" size="small" effect="plain" class="perm-tag">
            {{ permText(perm) }}
          </el-tag>
        </div>
        <div class="ai-line">
          <el-icon><MagicStick /></el-icon>
          AI：{{ role.ai_prompts.join(' / ') }}
        </div>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索用户名 / 姓名"
          clearable
          style="width: 220px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="query.role" placeholder="全部角色" clearable style="width: 150px" @change="search">
          <el-option v-for="r in roles" :key="r.role" :label="r.name" :value="r.role" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="username" label="用户名" width="140" />
        <el-table-column prop="full_name" label="姓名" width="120" />
        <el-table-column label="手机号" width="140">
          <template #default="{ row }">{{ row.phone || '—' }}</template>
        </el-table-column>
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag :color="roleMap[row.role]?.color" effect="dark" size="small" style="border: none">
              {{ row.role_name }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small" effect="plain">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ row.created_at?.replace('T', ' ').slice(0, 19) }}</template>
        </el-table-column>
        <el-table-column label="操作" min-width="180">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" @click="openReset(row)">重置密码</el-button>
            <el-button
              link
              type="danger"
              :disabled="row.id === userStore.user?.id"
              @click="remove(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="loadUsers"
        />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑用户' : '新建用户'" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" :disabled="editing" placeholder="登录账号" />
        </el-form-item>
        <el-form-item v-if="!editing" label="初始密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="姓名" prop="full_name">
          <el-input v-model="form.full_name" placeholder="真实姓名" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width: 100%">
            <el-option v-for="r in roles" :key="r.role" :label="r.name" :value="r.role">
              <span>{{ r.name }}</span>
              <span class="opt-desc">{{ r.description }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item v-if="editing" label="账号状态">
          <el-switch v-model="form.is_active" active-text="启用" inactive-text="禁用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="pwdVisible" title="重置密码" width="420px">
      <el-form label-width="80px">
        <el-form-item label="用户">{{ pwdForm.username }}</el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="pwdForm.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdVisible = false">取消</el-button>
        <el-button type="primary" @click="submitReset">确定</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="staffVisible" title="手机号开户" width="500px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="验证码将发送到员工手机，验证通过后账号立即启用，无需再审核"
        class="staff-tip"
      />
      <el-form ref="staffFormRef" :model="staffForm" :rules="staffRules" label-width="90px">
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="staffForm.phone" placeholder="11 位手机号" maxlength="11" />
        </el-form-item>
        <el-form-item label="验证码" prop="code">
          <div class="code-row">
            <el-input v-model="staffForm.code" placeholder="6 位验证码" maxlength="6" />
            <el-button :disabled="staffCountdown > 0" :loading="staffSending" class="code-btn" @click="sendStaffCode">
              {{ staffCountdown > 0 ? `${staffCountdown}s 后重发` : '获取验证码' }}
            </el-button>
          </div>
        </el-form-item>
        <el-alert
          v-if="staffDevCode"
          type="warning"
          :closable="false"
          show-icon
          :title="`演练模式：验证码 ${staffDevCode} 已自动填入`"
          class="staff-tip"
        />
        <el-form-item label="登录账号">
          <el-input v-model="staffForm.username" placeholder="留空则使用手机号登录" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="staffForm.full_name" placeholder="员工姓名" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input v-model="staffForm.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="staffForm.role" style="width: 100%">
            <el-option v-for="r in roles" :key="r.role" :label="r.name" :value="r.role">
              <span>{{ r.name }}</span>
              <span class="opt-desc">{{ r.description }}</span>
            </el-option>
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="staffVisible = false">取消</el-button>
        <el-button type="primary" :loading="staffSubmitting" @click="submitStaff">开通账号</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 10px;
}

.code-row {
  display: flex;
  gap: 10px;
  width: 100%;
}

.code-btn {
  width: 128px;
  flex-shrink: 0;
}

.staff-tip {
  margin-bottom: 14px;
}

.role-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}

.role-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
}

.role-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.role-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.role-head strong {
  font-size: 14px;
}

.role-desc {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
  min-height: 38px;
}

.perm-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.perm-tag {
  font-size: 11px;
}

.ai-line {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  color: var(--text-secondary);
  display: flex;
  gap: 4px;
  align-items: flex-start;
}

.ai-line .el-icon {
  color: var(--brand);
  margin-top: 2px;
  flex-shrink: 0;
}

.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.opt-desc {
  float: right;
  font-size: 11px;
  color: #9ca3af;
}

@media (max-width: 1300px) {
  .role-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
