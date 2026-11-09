<script setup>
/** 扫码录单输入框：扫码枪回车或手输条码，命中后把商品抛给父组件。 */
import { nextTick, ref } from 'vue'
import { productApi } from '../api'

const props = defineProps({
  placeholder: { type: String, default: '扫描条码或输入条码后回车' },
  // 可选：限定物料类型（raw_material / finished_good），不匹配时提示
  productType: { type: String, default: '' },
  // 连续扫描：扫完自动清空并重新聚焦，扫码枪可以一直扫不用点鼠标
  keepFocus: { type: Boolean, default: true },
})
const emit = defineEmits(['select'])

const code = ref('')
const loading = ref(false)
const inputRef = ref(null)

function refocus() {
  if (!props.keepFocus) return
  nextTick(() => inputRef.value?.focus())
}

async function submit() {
  const value = code.value.trim()
  if (!value || loading.value) return
  loading.value = true
  try {
    const product = await productApi.scan(value)
    if (props.productType && product.product_type !== props.productType) {
      const name = product.product_type === 'raw_material' ? '原料' : '成品'
      // 扫码结果与单据类型不符时直接拦下，避免录错行
      const { ElMessage } = await import('element-plus')
      ElMessage.warning(`「${product.name}」是${name}，与当前单据不匹配`)
      return
    }
    emit('select', product)
    code.value = ''
  } catch {
    // http 拦截器已提示「未找到条码」，这里只清空输入便于连续扫
    code.value = ''
  } finally {
    loading.value = false
    refocus()
  }
}

defineExpose({ focus: refocus })
</script>

<template>
  <el-input
    ref="inputRef"
    v-model="code"
    :placeholder="placeholder"
    clearable
    class="scan-input"
    @keyup.enter="submit"
  >
    <template #prefix><el-icon><Aim /></el-icon></template>
    <template #append>
      <el-button :loading="loading" @click="submit">添加</el-button>
    </template>
  </el-input>
</template>

<style scoped>
.scan-input {
  width: 280px;
}
</style>
