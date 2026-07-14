import { useMemo, useState } from 'react'

const colors = ['#f4772e', '#2563eb', '#16a36a', '#8b5cf6', '#d49b00']

function polygonPath(polygon) {
  const ring = (points) => `${points.map((point, index) => `${index ? 'L' : 'M'}${point[0]},${point[1]}`).join(' ')} Z`
  return [ring(polygon.outer), ...polygon.holes.map(ring)].join(' ')
}

function Layout({ result }) {
  const [layer, setLayer] = useState(1)
  const current = result.layers.find((item) => item.layer === layer) || result.layers[0]
  const dimensions = result.carton.inner_dimensions_cm
  return (
    <section className="irregular-result">
      <div className="irregular-metrics">
        <div><span>箱体内尺寸</span><strong>{dimensions.length_cm} × {dimensions.width_cm} × {dimensions.height_cm} cm</strong></div>
        <div><span>最低成本</span><strong>HKD {result.costs_hkd.selected_total.toFixed(2)}</strong></div>
        <div><span>利用率</span><strong>{(result.utilization * 100).toFixed(1)}%</strong></div>
        <div><span>求解器</span><strong>{result.solver.name}</strong></div>
      </div>
      <p className="lane-notice">计价线路：{result.pricing_lane_used}。{result.cost_scope === 'shipping_only' ? '当前只包含运输成本。' : '已加上已配置的包装相关成本。'}</p>
      <div className="layer-buttons">{result.layers.map((item) => <button type="button" className={item.layer === layer ? 'active' : ''} onClick={() => setLayer(item.layer)} key={item.layer}>第 {item.layer} 层</button>)}</div>
      <svg className="layout-2d" viewBox={`0 0 ${dimensions.length_cm} ${dimensions.width_cm}`} role="img" aria-label={`第 ${layer} 层二维排布`}>
        <rect x="0" y="0" width={dimensions.length_cm} height={dimensions.width_cm} fill="#f8fafc" stroke="#334155" strokeWidth=".4"/>
        {current.placements.flatMap((placement, index) => placement.polygons.map((polygon, polygonIndex) => (
          <path key={`${placement.unit_id}-${placement.instance}-${polygonIndex}`} d={polygonPath(polygon)} fill={`${colors[index % colors.length]}66`} stroke={colors[index % colors.length]} strokeWidth=".35" fillRule="evenodd"/>
        )))}
        {current.placements.map((placement) => <text key={`${placement.unit_id}-${placement.instance}`} x={placement.x_cm} y={placement.y_cm} fontSize="2.5">{placement.unit_id} #{placement.instance} / {placement.rotation_deg}°</text>)}
      </svg>
      <p className={result.verification.valid ? 'verified' : 'error-banner'}>{result.verification.valid ? `几何复验通过，共 ${result.verification.placement_count} 件。` : result.verification.errors.join('；')}</p>
    </section>
  )
}

export default function IrregularWorkflow({ apiBase = '' }) {
  const [files, setFiles] = useState({})
  const [inspection, setInspection] = useState(null)
  const [units, setUnits] = useState([])
  const [config, setConfig] = useState({ item_clearance_cm: 1.5, edge_margin_cm: 2, max_layers: 1, rotation_step_deg: 5 })
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const canSolve = useMemo(() => units.length > 0 && units.every((item) => Number(item.thickness_cm) > 0 && Number(item.weight_kg) > 0), [units])

  async function inspect(event) {
    event.preventDefault()
    setBusy(true); setError(''); setInspection(null); setResult(null)
    const form = new FormData()
    form.append('cdr_file', files.cdr)
    if (files.xlsx) form.append('production_sheet', files.xlsx)
    if (files.png) form.append('reference_image', files.png)
    if (files.svg) form.append('svg_file', files.svg)
    try {
      const response = await fetch(`${apiBase}/pack/irregular-2d/inspect`, { method: 'POST', body: form })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '文件检查失败')
      setInspection(data)
      setUnits(data.packing_units.map((item) => ({ ...item, thickness_cm: item.thickness_cm || '', weight_kg: item.weight_kg || '' })))
    } catch (requestError) { setError(requestError.message) } finally { setBusy(false) }
  }

  function updateUnit(index, key, value) {
    setUnits((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item))
  }

  async function solve() {
    setBusy(true); setError(''); setResult(null)
    try {
      const response = await fetch(`${apiBase}/pack/irregular-2d/solve`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order_id: files.cdr.name.replace(/\.cdr$/i, ''), requested_destination: 'SG',
          units: units.map((item) => ({ ...item, thickness_cm: Number(item.thickness_cm), weight_kg: Number(item.weight_kg), requires_dimensions: undefined })),
          packing: { ...config, item_clearance_cm: Number(config.item_clearance_cm), edge_margin_cm: Number(config.edge_margin_cm), max_layers: Number(config.max_layers), rotation_step_deg: Number(config.rotation_step_deg) },
        }),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '二维排布失败')
      setResult(data)
    } catch (requestError) { setError(requestError.message) } finally { setBusy(false) }
  }

  return (
    <main className="irregular-page">
      <section className="intro"><div><span className="eyebrow">IRREGULAR SIGN NESTING</span><h1>灯牌轮廓，<br/><em>按真实形状装箱。</em></h1></div><p>CDR 与生产单用于判断整体、可拆和混合结构；经确认的 SVG 轮廓参与二维排布。</p></section>
      <form className="irregular-upload" onSubmit={inspect}>
        <div className="section-heading"><div><span className="eyebrow">01 / SOURCE</span><h2>导入与实体判断</h2></div></div>
        <div className="file-grid">
          <label><span>CDR 文件 *</span><input required type="file" accept=".cdr" onChange={(event) => setFiles((value) => ({ ...value, cdr: event.target.files[0] }))}/></label>
          <label><span>生产单 XLSX</span><input type="file" accept=".xlsx" onChange={(event) => setFiles((value) => ({ ...value, xlsx: event.target.files[0] }))}/></label>
          <label><span>参考 PNG</span><input type="file" accept="image/png" onChange={(event) => setFiles((value) => ({ ...value, png: event.target.files[0] }))}/></label>
          <label><span>CorelDRAW 导出 SVG</span><input type="file" accept="image/svg+xml,.svg" onChange={(event) => setFiles((value) => ({ ...value, svg: event.target.files[0] }))}/></label>
        </div>
        <button className="calculate-button" disabled={busy || !files.cdr}>{busy ? '处理中…' : '检查文件与结构'}</button>
      </form>
      {error && <div className="error-banner"><strong>处理未完成</strong><span>{error}</span></div>}
      {inspection && <section className="inspection-panel">
        <div className="section-heading"><div><span className="eyebrow">02 / REVIEW</span><h2>实体确认与物理参数</h2></div><span className="classification">{inspection.assembly.classification} · {(inspection.assembly.confidence * 100).toFixed(0)}%</span></div>
        <ul>{inspection.assembly.evidence.map((item, index) => <li key={index}>{item.fact}</li>)}</ul>
        {inspection.warnings.map((warning) => <p className="lane-notice" key={warning}>{warning}</p>)}
        {units.length > 0 ? <div className="unit-table">{units.map((unit, index) => <div key={unit.unit_id}><strong>{unit.name}</strong><span>{unit.role}</span><label>厚度 cm<input type="number" min="0.01" step="0.1" value={unit.thickness_cm} onChange={(event) => updateUnit(index, 'thickness_cm', event.target.value)}/></label><label>单件重量 kg<input type="number" min="0.01" step="0.1" value={unit.weight_kg} onChange={(event) => updateUnit(index, 'weight_kg', event.target.value)}/></label><label>数量<input type="number" min="1" value={unit.quantity} onChange={(event) => updateUnit(index, 'quantity', Number(event.target.value))}/></label></div>)}</div> : <p>请在 CorelDRAW 中把每个可独立移动实体命名为 <code>pack-*</code> 后导出 SVG，或由转换工作节点生成规范化 SVG。</p>}
        <div className="packing-settings"><label>件间净距 cm<input type="number" min="0" step="0.1" value={config.item_clearance_cm} onChange={(event) => setConfig({ ...config, item_clearance_cm: event.target.value })}/></label><label>箱边净距 cm<input type="number" min="0" step="0.1" value={config.edge_margin_cm} onChange={(event) => setConfig({ ...config, edge_margin_cm: event.target.value })}/></label><label>最大层数<input type="number" min="1" max="10" value={config.max_layers} onChange={(event) => setConfig({ ...config, max_layers: event.target.value })}/></label><label>旋转步长 °<input type="number" min="1" max="90" value={config.rotation_step_deg} onChange={(event) => setConfig({ ...config, rotation_step_deg: event.target.value })}/></label></div>
        <button type="button" className="calculate-button" disabled={busy || !canSolve} onClick={solve}>{busy ? '正在排布与计价…' : '计算最低成本方案'}</button>
      </section>}
      {result && <Layout result={result}/>}
    </main>
  )
}
