/**
 * 单据打印：在独立窗口渲染版式后调用浏览器打印。
 *
 * 支持传入「单据模板」配置（来自 /api/templates）：抬头、Logo、纸张、
 * 列显示/改列名、条款与签字栏都可配置；未传模板时沿用内置默认版式。
 */

function style(paper) {
  return `
  @page { size: ${paper || 'A4'}; margin: 16mm; }
  * { box-sizing: border-box; }
  body {
    font-family: "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", sans-serif;
    color: #1f2937;
    margin: 0;
    font-size: 12px;
    line-height: 1.6;
  }
  .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
  .brand img { max-height: 52px; max-width: 150px; object-fit: contain; }
  .company { color: #4b5563; font-size: 13px; letter-spacing: 1px; }
  h1 { font-size: 20px; margin: 0 0 4px; letter-spacing: 2px; }
  .subtitle { color: #6b7280; font-size: 12px; margin-bottom: 16px; }
  .meta {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px 24px;
    padding: 10px 12px;
    background: #f8fafc;
    border: 1px solid #e5e7eb;
    border-radius: 4px;
    margin-bottom: 14px;
  }
  .meta span { color: #6b7280; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { border: 1px solid #e5e7eb; padding: 6px 8px; }
  th { background: #f1f5f9; font-weight: 600; text-align: left; }
  tfoot td { font-weight: 600; }
  .totals { margin-top: 12px; text-align: right; font-size: 13px; }
  .totals div { margin-bottom: 2px; }
  .totals b { font-size: 16px; color: #b91c1c; }
  .remark { margin-top: 14px; color: #4b5563; white-space: pre-wrap; }
  .terms { margin-top: 8px; color: #6b7280; font-size: 11px; white-space: pre-wrap; }
  .sign {
    display: grid;
    grid-template-columns: repeat(${'{count}'}, 1fr);
    gap: 24px;
    margin-top: 48px;
    color: #6b7280;
  }
  .sign div { border-top: 1px solid #d1d5db; padding-top: 6px; text-align: center; }
`
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderCell(row, column) {
  const raw = typeof column.render === 'function' ? column.render(row) : row[column.key]
  return escapeHtml(column.format ? column.format(raw, row) : raw)
}

function templateColumns(columns, template) {
  const map = (template && template.config && template.config.columns) || {}
  return columns
    .filter((column) => column.key && map[column.key] !== false)
    .map((column) =>
      typeof map[column.key] === 'string' ? { ...column, label: map[column.key] } : column
    )
}

/**
 * 打开打印窗口。
 *
 * @param {object} options
 * @param {string} options.title 默认单据名称
 * @param {string} [options.subtitle] 单号等副标题
 * @param {Array<[string, unknown]>} [options.meta] 抬头信息
 * @param {Array<{label: string, key?: string, align?: string, format?: Function, render?: Function}>} options.columns 明细列
 * @param {Array<object>} options.rows 明细数据
 * @param {Array<{label: string, value: unknown, bold?: boolean}>} [options.totals] 金额汇总
 * @param {string} [options.remark] 备注
 * @param {object|null} [options.template] 单据模板（含 config）
 */
export function printDocument({
  title,
  subtitle = '',
  meta = [],
  columns = [],
  rows = [],
  totals = [],
  remark = '',
  template = null,
}) {
  const cfg = (template && template.config) || {}
  const paper = cfg.paper === 'A5' ? 'A5' : 'A4'
  const finalTitle = (template && template.title) || title
  const shownColumns = templateColumns(columns, template)
  const company = cfg.company || ''
  const logo = cfg.show_logo && cfg.logo ? cfg.logo : ''
  const signs = Array.isArray(cfg.signs) && cfg.signs.length
    ? cfg.signs.filter(Boolean)
    : ['制单', '审核', '客户签收']
  const terms = cfg.terms || ''

  const metaHtml = meta
    .map(([label, value]) => `<div><span>${escapeHtml(label)}：</span>${escapeHtml(value)}</div>`)
    .join('')

  const headHtml = shownColumns
    .map((column) => `<th style="text-align:${column.align || 'left'}">${escapeHtml(column.label)}</th>`)
    .join('')

  const bodyHtml = rows.length
    ? rows
        .map(
          (row) =>
            '<tr>' +
            shownColumns
              .map(
                (column) =>
                  `<td style="text-align:${column.align || 'left'}">${renderCell(row, column)}</td>`
              )
              .join('') +
            '</tr>'
        )
        .join('')
    : `<tr><td colspan="${Math.max(shownColumns.length, 1)}" style="text-align:center;color:#9ca3af">无明细</td></tr>`

  const totalsHtml = totals
    .map(
      (item) =>
        `<div>${escapeHtml(item.label)}：${
          item.bold ? `<b>${escapeHtml(item.value)}</b>` : escapeHtml(item.value)
        }</div>`
    )
    .join('')

  const headBlock = logo
    ? `<div class="brand"><img src="${escapeHtml(logo)}" alt="logo" /><div>
        ${company ? `<div class="company">${escapeHtml(company)}</div>` : ''}
        <h1>${escapeHtml(finalTitle)}</h1>
      </div></div>`
    : `<h1>${escapeHtml(finalTitle)}</h1>${company ? `<div class="company">${escapeHtml(company)}</div>` : ''}`

  const signHtml = signs.map((label) => `<div>${escapeHtml(label)}</div>`).join('')

  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(finalTitle)} ${escapeHtml(subtitle)}</title>
  <style>${style(paper).replace('{count}', Math.max(signs.length, 1))}</style>
</head>
<body>
  ${headBlock}
  <div class="subtitle">${escapeHtml(subtitle)}</div>
  ${metaHtml ? `<div class="meta">${metaHtml}</div>` : ''}
  <table>
    <thead><tr>${headHtml}</tr></thead>
    <tbody>${bodyHtml}</tbody>
  </table>
  ${totalsHtml ? `<div class="totals">${totalsHtml}</div>` : ''}
  ${remark ? `<div class="remark">备注：${escapeHtml(remark)}</div>` : ''}
  ${terms ? `<div class="terms">${escapeHtml(terms)}</div>` : ''}
  <div class="sign">${signHtml}</div>
</body>
</html>`

  const win = window.open('', '_blank', 'width=900,height=1000')
  if (!win) {
    return false
  }
  win.document.open()
  win.document.write(html)
  win.document.close()
  win.onload = () => {
    win.focus()
    win.print()
  }
  setTimeout(() => {
    if (!win.closed) {
      win.focus()
      win.print()
    }
  }, 300)
  return true
}
