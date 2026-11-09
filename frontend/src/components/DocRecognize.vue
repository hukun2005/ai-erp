<script setup>
/** 单据照片识别入口：选图、多页上传、调用识别、把结果抛给父组件填表。 */
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { visionApi } from '../api'

const props = defineProps({
  docType: { type: String, default: 'inbound' },
  maxPages: { type: Number, default: 5 },
  disabled: { type: Boolean, default: false },
  hint: { type: String, default: '' },
})

const emit = defineEmits(['recognized'])

const files = ref([])
const loading = ref(false)
const elapsed = ref(0)
let timer = null

function onPick(uploadFile) {
  if (!uploadFile?.raw) return
  if (files.value.length >= props.maxPages) {
    ElMessage.warning(`一次最多识别 ${props.maxPages} 页，请分次识别`)
    return
  }
  files.value.push({ raw: uploadFile.raw, name: uploadFile.name })
}

function removeFile(index) {
  files.value.splice(index, 1)
}

function stopTimer() {
  if (timer) clearInterval(timer)
  timer = null
  elapsed.value = 0
}

async function recognize() {
  if (!files.value.length) {
    ElMessage.warning('请先选择单据照片')
    return
  }
  loading.value = true
  elapsed.value = 0
  timer = setInterval(() => {
    elapsed.value += 1
  }, 1000)
  try {
    const result = await visionApi.recognize(
      props.docType,
      files.value.map((file) => file.raw),
    )
    files.value = []
    emit('recognized', result)
  } catch (error) {
    // 识别失败的具体原因由请求拦截器提示，这里只负责恢复按钮状态
    if (!error?.response) ElMessage.error('识别失败，请检查网络后重试')
  } finally {
    loading.value = false
    stopTimer()
  }
}

onBeforeUnmount(stopTimer)
</script>

<template>
  <div class="doc-recognize">
    <el-upload
      :show-file-list="false"
      :auto-upload="false"
      multiple
      accept="image/*"
      :disabled="disabled || loading"
      :on-change="onPick"
    >
      <el-button type="primary" plain :loading="loading" :disabled="disabled">
        <el-icon><Camera /></el-icon>&nbsp;
        {{ loading ? `识别中 ${elapsed}s` : '拍照 / 上传单据识别' }}
      </el-button>
    </el-upload>

    <div v-if="files.length" class="picked">
      <el-tag v-for="(file, index) in files" :key="index" closable size="small" @close="removeFile(index)">
        {{ file.name }}
      </el-tag>
      <el-button size="small" type="primary" :loading="loading" @click="recognize">
        识别 {{ files.length }} 页
      </el-button>
      <el-button size="small" link @click="files = []">清空</el-button>
    </div>

    <span class="hint">
      {{ hint || `支持多页（最多 ${maxPages} 张），图片会自动转正压缩；识别结果请逐行核对后再保存。` }}
    </span>
  </div>
</template>

<style scoped>
.doc-recognize {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.picked {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.hint {
  color: var(--text-secondary);
  font-size: 12px;
}
</style>
