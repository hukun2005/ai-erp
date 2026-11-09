/** 单据识别结果落到表单：字段统一、候选带出、一键采纳。 */

/** 把识别返回的明细转成各单据页共用的编辑行结构。 */
export function mapRecognizedItems(items, extra = () => ({})) {
  return (items || []).map((item) => ({
    product_id: item.product_id || null,
    quantity: Math.max(1, Number(item.quantity) || 1),
    unit_price: Number(item.unit_price) || 0,
    raw_name: item.raw_name || '',
    raw_spec: item.raw_spec || '',
    raw_unit: item.raw_unit || '',
    match_status: item.match_status || 'unmatched',
    candidates: item.candidates || [],
    remark: item.product_id ? '' : `识别为：${item.raw_name || item.raw_sku || '未知商品'}`,
    ...extra(item),
  }))
}

/** 识别结果里的照片与识别原文，保存单据时一并提交。 */
export function photoFields(result) {
  return {
    photo_path: result?.photo_path || '',
    photo_name: result?.photo_name || '',
    photo_paths: result?.photo_paths || [],
    ocr_status: result?.ocr_status || 'manual',
    ocr_raw: result?.raw_text || '',
  }
}

/** 未匹配的行按候选顺序补上第一个商品，返回采纳了几行。 */
export function adoptCandidates(items) {
  let adopted = 0
  for (const item of items || []) {
    if (item.product_id || !item.candidates?.length) continue
    item.product_id = item.candidates[0].product_id
    item.match_status = 'matched'
    item.remark = ''
    adopted += 1
  }
  return adopted
}

/** 一行商品改了选择后，状态与备注同步成「已匹配」。 */
export function markMatched(item) {
  if (!item?.product_id) return
  item.match_status = 'matched'
  if (String(item.remark || '').startsWith('识别为：')) item.remark = ''
}

/** 保存时的明细字段：raw_name 只用于后端记别名，不落明细表。 */
export function itemPayload(item) {
  return {
    product_id: item.product_id,
    quantity: Number(item.quantity) || 0,
    unit_price: Number(item.unit_price) || 0,
    raw_name: item.raw_name || '',
  }
}

/** 行状态标签的文案与颜色。 */
export function matchTag(item) {
  if (item?.match_status === 'matched') return { text: '已匹配', type: 'success' }
  if (item?.candidates?.length) return { text: '待确认', type: 'warning' }
  return { text: '未匹配', type: 'danger' }
}
