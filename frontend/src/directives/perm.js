import { useUserStore } from '../stores/user'

/** v-perm="'product:manage'" 或 v-perm="['sales:manage', 'purchase:manage']"，无权限则移除元素 */
export default {
  mounted(el, binding) {
    const store = useUserStore()
    const value = Array.isArray(binding.value) ? binding.value : [binding.value]
    if (!value.some((perm) => store.hasPerm(perm))) {
      el.parentNode?.removeChild(el)
    }
  },
}
