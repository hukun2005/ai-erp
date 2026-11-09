<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { printApi } from '../api'
import { openPdfBlob } from '../utils/pdf'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  docType: { type: String, required: true },
  ids: { type: Array, default: () => [] },
  title: { type: String, default: '打印单据' },
})
const emit = defineEmits(['update:modelValue'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const form = reactive({
  copies: 1,
  labels: '存根,客户,财务',
  watermark: '',
  paper: 'A4',
  width_mm: 241,
  height_mm: 140,
  offset_x_mm: 0,
  offset_y_mm: 0,
  barcode: true,
})
const busy = ref(false)
const custom = computed(() => form.paper === 'CUSTOM')

watch(
  () => props.modelValue,
  (value) => {
    if (value) form.watermark = ''
  },
)

function payload() {
  return {
    doc_type: props.docType,
    ids: props.ids,
    copies: form.copies,
    copy_labels:
      form.copies > 1
        ? form.labels.split(',').map((item) => item.trim()).filter(Boolean)
        : [],
    watermark: form.watermark,
    paper: custom.value ? 'A4' : form.paper,
    width_mm: custom.value ? form.width_mm : null,
    height_mm: custom.value ? form.height_mm : null,
    offset_x_mm: form.offset_x_mm,
    offset_y_mm: form.offset_y_mm,
    barcode: form.barcode,
  }
}

async function preview() {
  if (!props.ids.length) {
    ElMessage.warning('请先选择要打印的单据')
    return
  }
  busy.value = true
  try {
    const blob = await printApi.batch(payload())
    openPdfBlob(blob)
    visible.value = false
  } finally {
    busy.value = false
  }
}

async function calibration() {
  busy.value = true
  try {
    const blob = await printApi.calibration({
      offset_x_mm: form.offset_x_mm,
      offset_y_mm: form.offset_y_mm,
    })
    openPdfBlob(blob)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="560px">
    <el-form label-width="96px">
      <el-form-item label="单据数量">
        <span class="count-text">{{ ids.length }} 张</span>
      </el-form-item>
      <el-form-item label="打印份数">
        <el-input-number v-model="form.copies" :min="1" :max="8" />
        <span class="hint">多联时每份一页，并在页眉标注联次</span>
      </el-form-item>
      <el-form-item v-if="form.copies > 1" label="联次名称">
        <el-input v-model="form.labels" placeholder="存根,客户,财务" />
      </el-form-item>
      <el-form-item label="水印">
        <el-input v-model="form.watermark" placeholder="留空不加水印，例如：内部 / 样品" maxlength="20" />
      </el-form-item>
      <el-form-item label="纸张">
        <el-select v-model="form.paper" style="width: 200px">
          <el-option label="A4" value="A4" />
          <el-option label="A5" value="A5" />
          <el-option label="241×140 针式" value="CUSTOM" />
        </el-select>
      </el-form-item>
      <template v-if="custom">
        <el-form-item label="纸张尺寸">
          <el-input-number v-model="form.width_mm" :min="50" :max="600" :controls="false" style="width: 100px" />
          <span class="hint">×</span>
          <el-input-number v-model="form.height_mm" :min="50" :max="600" :controls="false" style="width: 100px" />
          <span class="hint">mm</span>
        </el-form-item>
      </template>
      <el-form-item label="套打偏移">
        <el-input-number v-model="form.offset_x_mm" :min="-30" :max="30" :step="0.5" style="width: 110px" />
        <span class="hint">X mm</span>
        <el-input-number v-model="form.offset_y_mm" :min="-30" :max="30" :step="0.5" style="width: 110px" />
        <span class="hint">Y mm</span>
      </el-form-item>
      <el-form-item label="单号条码">
        <el-switch v-model="form.barcode" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :loading="busy" @click="calibration">打印校准页</el-button>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="preview">预览并打印</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--text-muted);
}

.count-text {
  font-weight: 600;
}
</style>
