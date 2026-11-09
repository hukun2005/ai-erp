<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouteKeyword } from '../utils/routeQuery'
import { ElMessage, ElMessageBox } from 'element-plus'
import { customerApi, creditApi } from '../api'
import { saveBlob } from '../utils/download'
import { useQuickCreate } from '../utils/quickCreate'
import { useUserStore } from '../stores/user'

const userStore = useUserStore()

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '', level: '' })
useRouteKeyword(query, () => {
  page.value = 1
  load()
})

const dialogVisible = ref(false)
const editing = ref(false)
const formRef = ref()
const blank = {
  name: '',
  contact: '',
  phone: '',
  email: '',
  address: '',
  level: 'C',
  credit_limit: 0,
  remark: '',
}
const form = reactive({ ...blank })

const rules = { name: [{ required: true, message: '请输入客户名称', trigger: 'blur' }] }

async function load() {
  loading.value = true
  try {
    const data = await customerApi.list({ ...query, page: page.value, page_size: 20 })
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

// 顶栏「新建」入口带 ?new=1 时直接打开新建客户弹窗
useQuickCreate(openCreate)

function openCreate() {
  editing.value = false
  Object.assign(form, blank)
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = true
  Object.assign(form, row)
  dialogVisible.value = true
}

async function submit() {
  await formRef.value.validate()
  if (editing.value) {
    await customerApi.update(form.id, form)
    ElMessage.success('客户已更新')
  } else {
    await customerApi.create({ ...form })
    ElMessage.success('客户已创建')
  }
  dialogVisible.value = false
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除客户「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await customerApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

const creditVisible = ref(false)
const creditLoading = ref(false)
const creditInfo = ref(null)

async function showCredit(row) {
  creditVisible.value = true
  creditLoading.value = true
  creditInfo.value = null
  try {
    creditInfo.value = await creditApi.detail(row.id)
  } finally {
    creditLoading.value = false
  }
}

const importVisible = ref(false)
const importResult = ref(null)
const importing = ref(false)

function openImport() {
  importResult.value = null
  importVisible.value = true
}

async function downloadTemplate() {
  saveBlob(await customerApi.templateUrl(), '客户导入模板.xlsx')
}

async function onImportFile(uploadFile) {
  const file = uploadFile?.raw
  if (!file) return
  importing.value = true
  try {
    const result = await customerApi.import(file)
    importResult.value = result
    ElMessage.success(`导入完成：新增 ${result.created} 条`)
    if (result.created) load()
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">客户管理</h1>
        <p class="page-subtitle">维护客户档案与信用额度，AI 会自动识别流失风险客户</p>
        <el-tag v-if="userStore.dataScope === 'own'" size="small" type="warning" effect="plain" style="margin-top: 6px">
          数据范围：仅本人负责的客户 + 公海客户
        </el-tag>
      </div>
      <div style="display: flex; gap: 8px">
        <el-button v-perm="'customer:manage'" @click="openImport"><el-icon><Upload /></el-icon>&nbsp;批量导入</el-button>
        <el-button v-perm="'customer:manage'" type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建客户</el-button>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索名称 / 联系人 / 电话" clearable style="width: 260px" @keyup.enter="search" @clear="search" />
        <el-select v-model="query.level" placeholder="全部等级" clearable style="width: 140px" @change="search">
          <el-option label="A 级" value="A" />
          <el-option label="B 级" value="B" />
          <el-option label="C 级" value="C" />
        </el-select>
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="name" label="客户名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="等级" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.level === 'A' ? 'danger' : row.level === 'B' ? 'warning' : 'info'" effect="light">
              {{ row.level }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="contact" label="联系人" width="100" />
        <el-table-column prop="phone" label="电话" width="130" />
        <el-table-column label="信用额度" width="120" align="right">
          <template #default="{ row }">¥{{ row.credit_limit.toLocaleString() }}</template>
        </el-table-column>
        <el-table-column prop="address" label="地址" min-width="180" show-overflow-tooltip />
        <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="info" @click="showCredit(row)">信用</el-button>
            <el-button v-perm="'customer:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-perm="'customer:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="importVisible" title="批量导入客户" width="660px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="请先下载模板按表头填写后上传。客户名称重复的行自动跳过；销售员导入的客户自动归本人负责。"
      />
      <div style="display: flex; gap: 12px; align-items: center; margin: 14px 0">
        <el-button @click="downloadTemplate"><el-icon><Download /></el-icon>&nbsp;下载模板</el-button>
        <el-upload :show-file-list="false" :auto-upload="false" accept=".xlsx" :on-change="onImportFile">
          <el-button type="primary" :loading="importing">
            <el-icon><Upload /></el-icon>&nbsp;选择文件并导入
          </el-button>
        </el-upload>
      </div>
      <template v-if="importResult">
        <p style="font-size: 13px; color: var(--text-secondary); margin: 0 0 10px">
          新增 <b style="color: #16a34a">{{ importResult.created }}</b> 条，
          跳过 {{ importResult.skipped }} 条，
          失败 {{ importResult.error_count - importResult.skipped }} 条
        </p>
        <el-table v-if="importResult.errors.length" :data="importResult.errors" size="small" max-height="240">
          <el-table-column prop="row" label="行号" width="80" />
          <el-table-column prop="reason" label="原因" min-width="200" show-overflow-tooltip />
        </el-table>
      </template>
    </el-dialog>

    <el-dialog v-model="creditVisible" title="信用额度使用情况" width="480px">
      <div v-loading="creditLoading">
        <template v-if="creditInfo">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="客户">{{ creditInfo.customer_name }}</el-descriptions-item>
            <el-descriptions-item label="信用额度">¥{{ creditInfo.credit_limit.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="已占用">¥{{ creditInfo.outstanding.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="可用额度">
              <span :style="{ color: creditInfo.available < 0 ? '#ef4444' : '#16a34a' }">
                ¥{{ creditInfo.available.toLocaleString() }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="使用率">{{ creditInfo.usage_percent }}%</el-descriptions-item>
            <el-descriptions-item label="当前策略">
              {{ { off: '不检查', warn: '仅提示', block: '超额拦截' }[creditInfo.policy] }}
            </el-descriptions-item>
          </el-descriptions>
          <el-alert
            v-if="creditInfo.over_limit"
            type="error"
            :closable="false"
            show-icon
            style="margin-top: 12px"
            title="该客户已超出信用额度：block 策略下无法确认新订单出库"
          />
          <el-alert
            v-else-if="!creditInfo.enabled"
            type="info"
            :closable="false"
            show-icon
            style="margin-top: 12px"
            title="未设置信用额度，视为不做管控"
          />
        </template>
      </div>
    </el-dialog>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑客户' : '新建客户'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="客户名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="联系人"><el-input v-model="form.contact" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="电话"><el-input v-model="form.phone" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="客户等级">
              <el-select v-model="form.level" style="width: 100%">
                <el-option label="A 级" value="A" />
                <el-option label="B 级" value="B" />
                <el-option label="C 级" value="C" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="信用额度">
          <el-input-number v-model="form.credit_limit" :min="0" :step="10000" style="width: 100%" />
        </el-form-item>
        <el-form-item label="地址"><el-input v-model="form.address" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
