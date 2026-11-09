<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouteKeyword } from '../utils/routeQuery'
import { ElMessage, ElMessageBox } from 'element-plus'
import { supplierApi } from '../api'

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const query = reactive({ keyword: '' })
useRouteKeyword(query, () => {
  page.value = 1
  load()
})

const dialogVisible = ref(false)
const editing = ref(false)
const formRef = ref()
const blank = { name: '', contact: '', phone: '', email: '', address: '', supply_category: '', remark: '' }
const form = reactive({ ...blank })
const rules = { name: [{ required: true, message: '请输入供应商名称', trigger: 'blur' }] }

async function load() {
  loading.value = true
  try {
    const data = await supplierApi.list({ ...query, page: page.value, page_size: 20 })
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
    await supplierApi.update(form.id, form)
    ElMessage.success('供应商已更新')
  } else {
    await supplierApi.create({ ...form })
    ElMessage.success('供应商已创建')
  }
  dialogVisible.value = false
  load()
}

async function remove(row) {
  await ElMessageBox.confirm(`确定删除供应商「${row.name}」吗？`, '删除确认', { type: 'warning' })
  await supplierApi.remove(row.id)
  ElMessage.success('已删除')
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">供应商管理</h1>
        <p class="page-subtitle">AI 补货建议会优先匹配同品类供应商</p>
      </div>
      <el-button v-perm="'supplier:manage'" type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建供应商</el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input v-model="query.keyword" placeholder="搜索供应商 / 联系人" clearable style="width: 260px" @keyup.enter="search" @clear="search" />
        <el-button @click="search">查询</el-button>
      </div>

      <el-table :data="list" v-loading="loading">
        <el-table-column prop="name" label="供应商名称" min-width="180" show-overflow-tooltip />
        <el-table-column prop="supply_category" label="供货品类" width="120" />
        <el-table-column prop="contact" label="联系人" width="110" />
        <el-table-column prop="phone" label="电话" width="130" />
        <el-table-column prop="email" label="邮箱" min-width="170" show-overflow-tooltip />
        <el-table-column prop="address" label="地址" min-width="150" show-overflow-tooltip />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button v-perm="'supplier:manage'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-perm="'supplier:manage'" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="load" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑供应商' : '新建供应商'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="名称" prop="name"><el-input v-model="form.name" /></el-form-item>
        <el-row :gutter="14">
          <el-col :span="12">
            <el-form-item label="联系人"><el-input v-model="form.contact" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="电话"><el-input v-model="form.phone" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="供货品类"><el-input v-model="form.supply_category" placeholder="如 五金工具" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item>
          </el-col>
        </el-row>
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
