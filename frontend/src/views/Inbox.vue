<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { inboxApi } from '../api'

const filter = ref('all') // all / unread
const loading = ref(false)
const items = ref([])
const unread = ref(0)
const limit = 100

const CATEGORY_TYPE = { approval: 'primary', sla: 'warning', stock: 'danger' }

const visibleItems = computed(() =>
  filter.value === 'unread' ? items.value.filter((item) => !item.is_read) : items.value
)

function fmtTime(value) {
  return value ? value.replace('T', ' ').slice(0, 16) : ''
}

async function load() {
  loading.value = true
  try {
    const [list, count] = await Promise.all([
      inboxApi.list({ limit }),
      inboxApi.unreadCount(),
    ])
    items.value = list.items || []
    unread.value = count.unread || 0
  } finally {
    loading.value = false
  }
}

async function markRead(item) {
  if (item.is_read) return
  item.is_read = true
  try {
    await inboxApi.markRead(item.id)
    unread.value = Math.max(0, unread.value - 1)
  } catch {
    item.is_read = false
  }
}

async function markAllRead() {
  if (!unread.value) return
  await inboxApi.markAllRead()
  items.value.forEach((item) => (item.is_read = true))
  unread.value = 0
  ElMessage.success('已全部标记为已读')
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">消息中心</h1>
        <p class="page-subtitle">
          审批提交、审批超时与库存预警统一收口到这里，未读数同步显示在顶栏铃铛。
        </p>
      </div>
      <el-button type="primary" plain :disabled="!unread" @click="markAllRead">
        <el-icon><Finished /></el-icon>&nbsp;全部已读
      </el-button>
    </div>

    <div class="card">
      <div class="toolbar">
        <el-radio-group v-model="filter" size="small">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="unread">未读（{{ unread }}）</el-radio-button>
        </el-radio-group>
        <el-button size="small" :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon>&nbsp;刷新
        </el-button>
      </div>

      <div v-if="visibleItems.length" class="list">
        <div
          v-for="item in visibleItems"
          :key="item.id"
          class="msg"
          :class="{ unread: !item.is_read }"
          @click="markRead(item)"
        >
          <el-tag :type="CATEGORY_TYPE[item.category] || 'info'" size="small" effect="plain">
            {{ item.category_label }}
          </el-tag>
          <div class="msg-main">
            <div class="msg-title">{{ item.title }}</div>
            <div class="msg-body">{{ item.body }}</div>
          </div>
          <div class="msg-side">
            <span v-if="!item.is_read" class="dot"></span>
            <span class="msg-time">{{ fmtTime(item.created_at) }}</span>
          </div>
        </div>
      </div>
      <div v-else class="empty-text">暂无消息</div>
    </div>
  </div>
</template>

<style scoped>
.list {
  display: flex;
  flex-direction: column;
}

.msg {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 8px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.15s;
}

.msg:last-child {
  border-bottom: none;
}

.msg:hover {
  background: var(--bg);
}

.msg.unread {
  background: #f8faff;
}

.msg-main {
  flex: 1;
  min-width: 0;
}

.msg-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.msg-body {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--text-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
}

.msg-side {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ef4444;
}

.msg-time {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
}
</style>