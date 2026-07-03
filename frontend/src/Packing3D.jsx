import { useEffect, useMemo, useRef, useState } from 'react'

const palette = ['#ff6b35', '#2563eb', '#22a06b', '#8b5cf6', '#eab308']

function cuboidMesh(position, size) {
  const x = position.x
  const y = position.y
  const z = position.z
  const sx = size.length
  const sy = size.width
  const sz = size.height
  return {
    x: [x, x + sx, x, x + sx, x, x + sx, x, x + sx],
    y: [y, y, y + sy, y + sy, y, y, y + sy, y + sy],
    z: [z, z, z, z, z + sz, z + sz, z + sz, z + sz],
    i: [0, 0, 4, 4, 0, 0, 2, 2, 0, 0, 1, 1],
    j: [1, 3, 6, 7, 4, 5, 3, 7, 2, 6, 5, 7],
    k: [3, 2, 7, 5, 5, 1, 7, 6, 6, 4, 7, 3],
  }
}

function wireframe(box) {
  const L = box.length_cm
  const W = box.width_cm
  const H = box.height_cm
  const edges = [
    [[0, 0, 0], [L, 0, 0]], [[L, 0, 0], [L, W, 0]],
    [[L, W, 0], [0, W, 0]], [[0, W, 0], [0, 0, 0]],
    [[0, 0, H], [L, 0, H]], [[L, 0, H], [L, W, H]],
    [[L, W, H], [0, W, H]], [[0, W, H], [0, 0, H]],
    [[0, 0, 0], [0, 0, H]], [[L, 0, 0], [L, 0, H]],
    [[L, W, 0], [L, W, H]], [[0, W, 0], [0, W, H]],
  ]
  const x = []
  const y = []
  const z = []
  edges.forEach(([a, b]) => {
    x.push(a[0], b[0], null)
    y.push(a[1], b[1], null)
    z.push(a[2], b[2], null)
  })
  return { x, y, z }
}

export default function Packing3D({ cartons }) {
  const plotRef = useRef(null)
  const [cartonIndex, setCartonIndex] = useState(0)
  const [step, setStep] = useState(0)
  const [rendererError, setRendererError] = useState(false)
  const carton = cartons[cartonIndex]

  const colorBySku = useMemo(() => {
    const skus = cartons.flatMap((item) => item.layout.map((entry) => entry.sku))
    return Object.fromEntries([...new Set(skus)].map((sku, index) => [sku, palette[index % palette.length]]))
  }, [cartons])

  useEffect(() => {
    setStep(0)
  }, [cartonIndex])

  useEffect(() => {
    if (!plotRef.current || !carton) return undefined
    if (!window.Plotly) {
      setRendererError(true)
      return undefined
    }
    setRendererError(false)
    const frame = wireframe(carton.dimensions_cm)
    const traces = [{
      type: 'scatter3d', mode: 'lines', ...frame,
      line: { color: '#334155', width: 4 }, hoverinfo: 'skip', showlegend: false,
    }]
    carton.layout.forEach((item, index) => {
      const mesh = cuboidMesh(item.position, item.placed_dims)
      traces.push({
        type: 'mesh3d', ...mesh,
        name: item.sku,
        color: colorBySku[item.sku],
        opacity: 0.88,
        flatshading: false,
        visible: index < step,
        lighting: { ambient: 0.82, diffuse: 0.45, specular: 0.08 },
        hovertemplate:
          `<b>${item.sku}</b><br>` +
          `坐标 (${item.position.x}, ${item.position.y}, ${item.position.z}) cm<br>` +
          `尺寸 ${item.placed_dims.length}×${item.placed_dims.width}×${item.placed_dims.height} cm<br>` +
          `方向 ${item.rotation}<extra></extra>`,
      })
    })
    const dims = carton.dimensions_cm
    const maxDimension = Math.max(dims.length_cm, dims.width_cm, dims.height_cm)
    window.Plotly.react(plotRef.current, traces, {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      margin: { l: 0, r: 0, t: 12, b: 0 },
      showlegend: true,
      legend: { x: 0.02, y: 0.98, bgcolor: 'rgba(255,255,255,.78)' },
      scene: {
        bgcolor: 'rgba(248,250,252,.62)',
        xaxis: { title: '长 / X (cm)', nticks: 6, gridcolor: '#dbe3ee' },
        yaxis: { title: '宽 / Y (cm)', nticks: 6, gridcolor: '#dbe3ee' },
        zaxis: { title: '高 / Z (cm)', nticks: 6, gridcolor: '#dbe3ee' },
        aspectratio: {
          x: Math.max(0.55, dims.length_cm / maxDimension),
          y: Math.max(0.55, dims.width_cm / maxDimension),
          z: Math.max(0.55, dims.height_cm / maxDimension),
        },
        camera: { eye: { x: 1.55, y: -1.8, z: 1.25 } },
      },
    }, { responsive: true, displaylogo: false })
    return () => window.Plotly?.purge(plotRef.current)
  }, [carton, colorBySku, step])

  if (!carton) return null

  return (
    <section className="visual-panel">
      <div className="section-heading visual-heading">
        <div>
          <span className="eyebrow">3D PACKING GUIDE</span>
          <h2>逐步装箱指南</h2>
        </div>
        <div className="carton-tabs" aria-label="纸箱选择">
          {cartons.map((item, index) => (
            <button
              type="button"
              className={index === cartonIndex ? 'carton-tab active' : 'carton-tab'}
              onClick={() => setCartonIndex(index)}
              key={item.reference}
            >箱 {index + 1}</button>
          ))}
        </div>
      </div>

      <div className="visual-meta">
        <strong>{carton.reference}</strong>
        <span>{carton.dimensions_cm.length_cm} × {carton.dimensions_cm.width_cm} × {carton.dimensions_cm.height_cm} cm</span>
        <span>{carton.actual_weight_kg} kg</span>
        <span>利用率 {(carton.utilization * 100).toFixed(1)}%</span>
      </div>

      <div className="step-toolbar">
        <button type="button" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>上一步</button>
        <button type="button" className="primary-mini" onClick={() => setStep(Math.min(carton.layout.length, step + 1))} disabled={step === carton.layout.length}>下一步</button>
        <button type="button" onClick={() => setStep(0)}>重置</button>
        <button type="button" onClick={() => setStep(carton.layout.length)}>显示全部</button>
        <span>步骤 {step}/{carton.layout.length}</span>
      </div>

      {rendererError ? (
        <div className="plot-error">3D 渲染组件加载失败，请检查网络后刷新。</div>
      ) : <div className="plot-canvas" ref={plotRef} />}

      <div className="instruction-strip">
        {carton.layout.map((item, index) => (
          <div className={index < step ? 'instruction active' : 'instruction'} key={`${item.sku}-${item.step}`}>
            <span className="step-number">{item.step}</span>
            <div>
              <strong>{item.sku}</strong>
              <p>坐标 ({item.position.x}, {item.position.y}, {item.position.z}) · {item.placed_dims.length}×{item.placed_dims.width}×{item.placed_dims.height} cm · {item.rotation}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
