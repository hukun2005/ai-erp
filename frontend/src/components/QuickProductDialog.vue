<script setup>
/** 识别到系统里没有的商品时就地建档：只填名称，SKU 由后端按日期生成。 */
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { productApi } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  preset: { type: Object, default: () => ({}) },
  productType: { type: String, default: 'raw_material' },
})

const emit = defineEmits(['update:modelValue', 'created'])

const form = reactive({ name: '', spec: '', unit: '件', product_type: 'raw_material' })
const saving = ref(false)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    Object.assign(form, {
      name: props.preset?.name || '',
      spec: props.preset?.spec || '',
      unit: props.preset?.unit || '件',
      product_type: props.productType,
    })
  },
)

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写商品名称')
    return
  }
  saving.value = true
  try {
    const product = await productApi.quick({
      name: form.name.trim(),
      spec: form.spec.trim(),
      unit: form.unit.trim() || '件',
      product_type: form.product_type,
    })
    ElMessage.success(`已新建商品 ${product.sku}，保存单据后会记住这个叫法`)
    emit('created', product)
    emit('update:modelValue', false)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog :model-value="modelValue" title="新建商品" width="480px" @update:model-value="emit('update:modelValue', $event)">
    <el-form label-width="80px">
      <el-form-item label="名称" required>
        <el-input v-model="form.name" placeholder="按单据原文填写，例如：不锈钢法兰盘" />
      </el-form-item>
      <el-form-item label="规格">
        <el-input v-model="form.spec" placeholder="例如：DN50 / 3mm" />
      </el-form-item>
      <el-form-item label="单位">
        <el-input v-model="form.unit" style="width: 120px" />
      </el-form-item>
      <el-form-item label="物料类型">
        <el-radio-group v-model="form.product_type">
          <el-radio value="raw_material">原料</el-radio>
          <el-radio value="finished_good">成品</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-alert type="info" :closable="false" show-icon title="SKU 会按「P+日期+序号」自动生成，之后可在商品页修改。" />
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存并匹配</el-button>
    </template>
  </el-dialog>
</template>
