<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { backupApi } from '../api'
import { saveBlob } from '../utils/download'

const loading = ref(false)
const creating = ref(false)
const list = ref([])

async function load() {
  loading.value = true
  try {
    const data = await backupApi.list()
    list.value = data.items || []
  } finally {
    loading.value = false
  }
}

function sizeText(size) {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

async function createBackup() {
  creating.value = true
  try {
    const data = await backupApi.create()
    ElMessage.success(`备份完成：${data.name}`)
    await load()
  } finally {
    creating.value = false
  }
}

async function downloadBackup(row) {
  const blob = await backupApi.download(row.name)
  saveBlob(blob, row.name)
}

async function restoreBackup(row) {
  try {
    await ElMessageBox.confirm(
      `恢复「${row.name}」将覆盖当前全部数据，且无法撤销。建议先下载一份当前备份再操作。是否继续？`,
      '恢复确认',
      { type: 'warning', confirmButtonText: '确认恢复', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await backupApi.restore(row.name)
  ElMessage.success('已恢复，请刷新页面查看最新数据')
  await load()
}

async function removeBackup(row) {
  try {
    await ElMessageBox.confirm(`删除备份「${row.name}」？删除后不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await backupApi.remove(row.name)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">数据备份</h1>
        <p class="page-subtitle">
          一键备份数据库与上传文件（模板、图片、入库单照片），可下载归档或恢复到某个历史节点。
        </p>
      </div>
      <div class="page-actions">
        <el-button type="primary" :loading="creating" @click="createBackup">立即备份</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="提示"
      description="恢复会覆盖当前全部数据，操作前请先下载一份当前备份。自动备份可在 .env 中设置 BACKUP_AUTO_ENABLED=1 与 BACKUP_AUTO_INTERVAL_HOURS 开启。"
      style="margin-bottom: 16px"
    />

    <el-table :data="list" v-loading="loading" border>
      <el-table-column prop="name" label="备份文件" min-width="220" />
      <el-table-column label="大小" width="120">
        <template #default="{ row }">{{ sizeText(row.size) }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="downloadBackup(row)">下载</el-button>
          <el-button link type="warning" @click="restoreBackup(row)">恢复</el-button>
          <el-button link type="danger" @click="removeBackup(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无备份，点击右上角「立即备份」生成第一份" />
      </template>
    </el-table>
  </div>
</template>