<script setup>
/** 识别到系统里没有的供应商/客户时就地建档，省得跳到档案页再回来。 */
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { customerApi, supplierApi } from '../api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  kind: { type: String, default: 'supplier' },
  presetName: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'created'])

const form = reactive({ name: '', contact: '', phone: '' })
const saving = ref(false)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    Object.assign(form, { name: props.presetName || '', contact: '', phone: '' })
  },
)

const label = () => (props.kind === 'customer' ? '客户' : '供应商')

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning(`请填写${label()}名称`)
    return
  }
  saving.value = true
  try {
    const api = props.kind === 'customer' ? customerApi : supplierApi
    const created = await api.create({
      name: form.name.trim(),
      contact: form.contact.trim(),
      phone: form.phone.trim(),
    })
    ElMessage.success(`已新建${label()} ${created.name}`)
    emit('created', created)
    emit('update:modelValue', false)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="`新建${label()}`"
    width="460px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form label-width="90px">
      <el-form-item :label="`${label()}名称`" required>
        <el-input v-model="form.name" placeholder="按单据原文填写" />
      </el-form-item>
      <el-form-item label="联系人">
        <el-input v-model="form.contact" />
      </el-form-item>
      <el-form-item label="电话">
        <el-input v-model="form.phone" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存并选用</el-button>
    </template>
  </el-dialog>
</template>
