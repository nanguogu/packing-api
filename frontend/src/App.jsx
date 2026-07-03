import { useEffect, useMemo, useState } from 'react'
import Packing3D from './Packing3D.jsx'

const API_BASE = import.meta.env.VITE_API_BASE || ''
const itemColors = ['#ff6b35', '#2563eb', '#22a06b', '#8b5cf6', '#eab308', '#0891b2']

const sampleItems = [
  { sku: 'ITEM-1', length_cm: 100, width_cm: 50, height_cm: 40, weight_kg: 5 },
  { sku: 'ITEM-2', length_cm: 20, width_cm: 10, height_cm: 10, weight_kg: 3 },
  { sku: 'ITEM-3', length_cm: 60, width_cm: 60, height_cm: 60, weight_kg: 4 },
]

const loadingMessages = ['正在生成全部合法分箱组合', '正在求解每个纸箱的三维布局', '正在计算 DHL、UPS、FedEx 费用', '正在选择订单总价最低方案']

function Icon({ name, size = 20 }) {
  const paths = {
    box: <><path d="m3 6.5 9-4 9 4-9 4-9-4Z"/><path d="m3 6.5 9 4 9-4V18l-9 4-9-4V6.5Z"/><path d="M12 10.5V22"/></>,
    plus: <><path d="M12 5v14M5 12h14"/></>,
    trash: <><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6"/></>,
    route: <><circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h3a4 4 0 0 0 4-4v-4a4 4 0 0 1 3-4"/></>,
    spark: <><path d="m12 3 1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3Z"/><path d="m19 16 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z"/></>,
    arrow: <><path d="M5 12h14M14 7l5 5-5 5"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function formatMoney(value) {
  return new Intl.NumberFormat('zh-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)
}

function ItemCard({ item, index, onChange, onRemove, removable }) {
  const fields = [
    ['length_cm', '长度', 'cm'], ['width_cm', '宽度', 'cm'],
    ['height_cm', '高度', 'cm'], ['weight_kg', '重量', 'kg'],
  ]
  return (
    <article className="item-card" style={{ '--item-color': itemColors[index % itemColors.length] }}>
      <div className="item-card-head">
        <div className="item-index"><span>{String(index + 1).padStart(2, '0')}</span></div>
        <label className="sku-field">
          <span>货物编号</span>
          <input value={item.sku} onChange={(event) => onChange('sku', event.target.value)} aria-label={`货物 ${index + 1} 编号`} />
        </label>
        <button type="button" className="icon-button danger" onClick={onRemove} disabled={!removable} aria-label={`删除 ${item.sku}`}><Icon name="trash" size={18}/></button>
      </div>
      <div className="dimension-grid">
        {fields.map(([key, label, unit]) => (
          <label key={key}>
            <span>{label}</span>
            <div className="unit-input">
              <input type="number" min="0.1" step="0.1" value={item[key]} onChange={(event) => onChange(key, event.target.value)} aria-label={`${item.sku} ${label}`} />
              <small>{unit}</small>
            </div>
          </label>
        ))}
      </div>
    </article>
  )
}

function CarrierTable({ shipping }) {
  const recommended = shipping.recommended.carrier
  return (
    <div className="table-wrap">
      <table className="carrier-table">
        <thead><tr><th>物流商</th><th>计费重</th><th>基础运费</th><th>附加费</th><th>燃油费</th><th>最终总价</th></tr></thead>
        <tbody>
          {shipping.carriers.map((carrier) => carrier.available ? (
            <tr className={carrier.carrier === recommended ? 'recommended-row' : ''} key={carrier.carrier}>
              <td><strong>{carrier.carrier}</strong>{carrier.carrier === recommended && <span className="best-pill">最低价</span>}</td>
              <td>{carrier.shipment_billable_weight_kg} kg</td>
              <td>HKD {formatMoney(carrier.base_rate)}</td>
              <td>HKD {formatMoney(carrier.surcharge_total || 0)}</td>
              <td>HKD {formatMoney(carrier.fuel_surcharge)}</td>
              <td><strong>HKD {formatMoney(carrier.total)}</strong></td>
            </tr>
          ) : (
            <tr key={carrier.carrier}><td><strong>{carrier.carrier}</strong></td><td colSpan="5">当前线路不可用</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Results({ result }) {
  const available = result.shipping.carriers.filter((carrier) => carrier.available).sort((a, b) => a.total - b.total)
  const recommendedCarrier = available.find((carrier) => carrier.carrier === result.recommendation.carrier)
  const saving = available.length > 1 ? available[1].total - available[0].total : 0
  return (
    <section className="results" id="results" data-testid="results">
      <div className="result-hero">
        <div>
          <span className="eyebrow light">OPTIMIZATION COMPLETE</span>
          <h2>已找到订单总价最低方案</h2>
          <p>已比较 {result.packing.partition_count} 种分箱组合、{result.packing.evaluated_plan_count} 个可报价方案。</p>
        </div>
        <div className="recommendation-price">
          <span>推荐 {result.recommendation.carrier}</span>
          <strong><small>HKD</small> {formatMoney(result.recommendation.shipping_total)}</strong>
          {saving > 0 && <em>较次优物流节省 HKD {formatMoney(saving)}</em>}
        </div>
      </div>

      <div className="metric-grid">
        <div className="metric-card"><span>推荐箱数</span><strong>{result.packing.carton_count}</strong><small>个定制纸箱</small></div>
        <div className="metric-card"><span>订单实重</span><strong>{result.packing.total_actual_weight_kg}</strong><small>kg</small></div>
        <div className="metric-card"><span>物流计费重</span><strong>{recommendedCarrier?.shipment_billable_weight_kg ?? '—'}</strong><small>kg</small></div>
        <div className="metric-card"><span>纸箱总体积</span><strong>{(result.packing.total_carton_volume_cm3 / 1000).toFixed(1)}</strong><small>升</small></div>
      </div>

      <section className="result-section">
        <div className="section-heading"><div><span className="eyebrow">CARTON STRATEGY</span><h2>最优订箱方案</h2></div></div>
        <div className="carton-grid">
          {result.packing.cartons.map((carton, index) => (
            <article className="carton-card" key={carton.reference}>
              <div className="carton-card-top"><span>纸箱 {index + 1}</span><small>{carton.layout_objective === 'volume' ? '最小体积布局' : '紧凑边长布局'}</small></div>
              <h3>{carton.dimensions_cm.length_cm} × {carton.dimensions_cm.width_cm} × {carton.dimensions_cm.height_cm} <small>cm</small></h3>
              <div className="carton-stats"><span>{carton.actual_weight_kg} kg</span><span>利用率 {(carton.utilization * 100).toFixed(1)}%</span></div>
              <div className="sku-chips">{carton.item_skus.map((sku) => <span key={sku}>{sku}</span>)}</div>
            </article>
          ))}
        </div>
      </section>

      <section className="result-section">
        <div className="section-heading"><div><span className="eyebrow">CARRIER COMPARISON</span><h2>三家物流费用对比</h2></div><span className="rate-note">公开价 · 香港至新加坡 · Priority</span></div>
        <CarrierTable shipping={result.shipping} />
      </section>

      <Packing3D cartons={result.packing.cartons} />

      {result.alternative_plans.length > 0 && (
        <section className="result-section alternatives">
          <div className="section-heading"><div><span className="eyebrow">ALTERNATIVES</span><h2>成本接近的备选方案</h2></div></div>
          <div className="alternative-grid">
            {result.alternative_plans.map((plan) => (
              <article key={plan.rank}>
                <span>备选 #{plan.rank}</span>
                <strong>{plan.carton_count} 箱 · {plan.carrier}</strong>
                <p>HKD {formatMoney(plan.shipping_total)}</p>
                <small>比推荐方案增加 HKD {formatMoney(plan.additional_cost)}</small>
                <div>{plan.carton_strategy.map((group, index) => <em key={index}>箱{index + 1}: {group.join(' + ')}</em>)}</div>
              </article>
            ))}
          </div>
        </section>
      )}
    </section>
  )
}

export default function App() {
  const [items, setItems] = useState(sampleItems)
  const [orderId, setOrderId] = useState('SG-DEMO-001')
  const [address, setAddress] = useState('Singapore Government Building')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingIndex, setLoadingIndex] = useState(0)

  useEffect(() => {
    if (!loading) return undefined
    const timer = window.setInterval(() => setLoadingIndex((index) => (index + 1) % loadingMessages.length), 1800)
    return () => window.clearInterval(timer)
  }, [loading])

  const validation = useMemo(() => {
    if (!orderId.trim()) return '请输入订单编号'
    if (items.length === 0) return '请至少添加一件货物'
    if (items.length > 5) return 'MVP 当前最多支持 5 件货物参与计算；已录入内容会保留'
    const skus = items.map((item) => item.sku.trim())
    if (skus.some((sku) => !sku)) return '每件货物都需要填写编号'
    if (new Set(skus).size !== skus.length) return '货物编号不能重复'
    if (items.some((item) => ['length_cm', 'width_cm', 'height_cm', 'weight_kg'].some((key) => Number(item[key]) <= 0))) return '长、宽、高和重量必须大于 0'
    return ''
  }, [items, orderId])

  function updateItem(index, key, value) {
    setItems((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item))
  }

  function addItem() {
    const index = items.length + 1
    setItems((current) => [...current, { sku: `ITEM-${index}`, length_cm: 30, width_cm: 20, height_cm: 10, weight_kg: 1 }])
  }

  function removeItem(index) {
    setItems((current) => current.filter((_, itemIndex) => itemIndex !== index))
  }

  async function calculate(event) {
    event.preventDefault()
    if (validation) return
    setLoading(true)
    setLoadingIndex(0)
    setError('')
    setResult(null)
    const payload = {
      order_id: orderId.trim(), origin: 'HK', destination: 'SG',
      destination_address: address.trim() || null, service_type: 'priority',
      time_limit_s: 1.5,
      items: items.map((item) => ({
        sku: item.sku.trim(),
        length_cm: Number(item.length_cm), width_cm: Number(item.width_cm),
        height_cm: Number(item.height_cm), weight_kg: Number(item.weight_kg),
      })),
    }
    try {
      const response = await fetch(`${API_BASE}/pack/level2`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || '计算失败，请检查输入后重试')
      setResult(data)
      window.setTimeout(() => document.getElementById('results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 120)
    } catch (requestError) {
      setError(requestError.message || '无法连接计算服务')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top"><span className="brand-mark"><Icon name="box"/></span><span>PACK<span>WISE</span></span></a>
        <div className="topbar-meta"><span className="status-dot"/>Level 2 成本优化引擎<span className="version">MVP · HK → SG</span></div>
      </header>

      <main id="top">
        <section className="intro">
          <div><span className="eyebrow">CUSTOM CARTON & LOGISTICS OPTIMIZER</span><h1>装箱与物流，<br/><em>一次算到最优。</em></h1></div>
          <p>录入货物尺寸与重量，系统将枚举全部分箱组合、求解三维布局，并比较 DHL、UPS 与 FedEx 的订单总价。</p>
        </section>

        <form onSubmit={calculate}>
          <section className="form-section cargo-section">
            <div className="section-heading">
              <div><span className="eyebrow">01 / CARGO</span><h2>货物信息</h2><p>可连续添加货物；MVP 当前支持 1–5 件参与计算。</p></div>
              <div className="heading-actions"><button type="button" className="ghost-button" disabled>导入尺寸表 <span>即将开放</span></button><button type="button" className="add-button" onClick={addItem}><Icon name="plus" size={18}/>添加货物</button></div>
            </div>
            <div className="item-list">
              {items.map((item, index) => <ItemCard key={index} item={item} index={index} onChange={(key, value) => updateItem(index, key, value)} onRemove={() => removeItem(index)} removable={items.length > 1}/>) }
            </div>
            {items.length > 5 && <div className="inline-alert"><Icon name="info"/>已添加 {items.length} 件。输入已保留，但 MVP 仅支持前端提交 1–5 件订单。</div>}
          </section>

          <section className="form-section logistics-section">
            <div className="section-heading"><div><span className="eyebrow">02 / LOGISTICS</span><h2>物流基础信息</h2><p>当前价卡固定为香港至新加坡 Priority，后续版本将开放更多线路和服务。</p></div></div>
            <div className="logistics-grid">
              <label><span>订单编号</span><input value={orderId} onChange={(event) => setOrderId(event.target.value)} placeholder="例如 SG-ORDER-001"/></label>
              <div className="route-card"><span>运输线路</span><div><b>香港</b><Icon name="arrow"/><b>新加坡</b></div><small>HK → SG</small></div>
              <div className="service-card"><span>服务档位</span><strong>Priority</strong><small>优先服务</small></div>
              <label className="address-field"><span>目的地详细地址</span><input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="输入新加坡详细地址"/></label>
            </div>
            <details className="future-options"><summary>查看后续版本将开放的物流选项</summary><div>{['始发地与邮编','目的国家、城市与邮编','Express / Economy','期望送达日期','空运 / 海运','公开价 / 企业协议价','包装类型与成本','声明价值与保险','偏远地区与住宅地址','贸易条款与税费'].map((item) => <span key={item}>{item}</span>)}</div></details>
          </section>

          <section className="calculate-bar">
            <div><Icon name="spark"/><span><strong>目标：订单物流总价最低</strong><small>同价时优先箱数更少、总体积更小</small></span></div>
            <div className="calculate-action">
              {validation && <p>{validation}</p>}
              <button type="submit" className="calculate-button" disabled={Boolean(validation) || loading} data-testid="calculate-button">
                {loading ? <span className="spinner"/> : <Icon name="spark"/>}
                {loading ? loadingMessages[loadingIndex] : '计算最优方案'}
              </button>
            </div>
          </section>
          {error && <div className="error-banner"><strong>计算未完成</strong><span>{error}</span></div>}
        </form>

        {loading && <section className="loading-panel"><div className="loading-orbit"><span/><span/><span/></div><h2>{loadingMessages[loadingIndex]}</h2><p>最多 5 件货物时，系统会完整比较所有合法分箱组合。</p></section>}
        {result && <Results result={result}/>} 
      </main>
      <footer><span>PACKWISE MVP</span><p>公开价仅用于方案比较，正式出货前请以承运商账单为准。</p></footer>
    </div>
  )
}
