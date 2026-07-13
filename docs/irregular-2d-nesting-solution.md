# 不规则灯牌二维装箱方案

## 1. 结论

本需求应作为现有三维长方体装箱之外的一条独立管线实现，而不是把灯牌近似成外接矩形后继续调用 OR-Tools CP-SAT。

建议的生产流程是：

1. 以 CDR 中的矢量曲线和物理单位作为几何真值。
2. 将 CDR 转换为规范化 SVG；PNG 和生产单用于辅助判断成品结构、人工对照和结果预览，不用于决定生产尺寸。
3. 根据物理轮廓、公共底板、生产工艺和对象关系生成“装箱实体图”：整体成品保持一个实体，可拆字母拆成多个实体，混合设计形成多个刚性子组件。
4. 将每个实际装箱实体转换为带孔多边形，并按包装间距向外膨胀。
5. 使用二维不规则排样引擎搜索每个灯牌的平移、任意允许角度旋转、孔洞嵌套和分层组合。
6. 枚举纸箱尺寸、层数和分箱方式，接入承运商报价及包装成本，选择总成本最低的方案。
7. 用原始高精度轮廓独立复验无重叠、不越界及层间包装约束。
8. 输出纸箱内尺寸、建议外尺寸、深度、利用率、每件灯牌的坐标/角度/层号，以及 SVG/HTML 摆放图。

不能直接承诺“数学上严格全局最小”。不规则排样是 NP-hard 问题，适合输出“在指定时间内找到的最佳方案”、面积下界和可复现的随机种子。对小规模订单可以增加较长求解时间，获得更接近全局最优的结果。

## 2. 参考项目评估

| 项目 | 结论 | 原因 |
| --- | --- | --- |
| `iyulab/u-nesting` | 首选集成候选，必须用真实订单做 POC | 二维模块支持 NFP、间距、离散旋转、GA/SA/ALNS，提供 JSON/C FFI，MIT 许可；但项目很新、尚无正式 release，公开基准不能替代本项目的真实曲线测试 |
| `MbBrainz/irregular-object-packing` | 不采用 | 面向任意 3D 容器和 3D 网格；仓库自述只支持单一形状、约 10 件，且没有 swapping/hole filling，并指出非线性约束实现存在问题，与本需求不匹配 |
| `JeroenGar/sparrow` + `jagua-rs` | 作为质量基准和备选引擎 | 专门求解二维不规则 strip packing，支持连续平移/旋转、孔洞和最小间距，能输出精确变换后的 SVG；Sparrow 为 MIT，Jagua 为 MPL-2.0，集成前需完成许可证审查 |
| Deepnest/SVGnest | 用作交互和结果质量参考，不建议直接嵌入 FastAPI | NFP + GA 的思路成熟，也支持 part-in-part；但现有实现主要是桌面/浏览器应用，服务化、资源终止、依赖和维护成本更高 |

POC 不应只比较“利用率”，至少比较：解析成功率、零碰撞正确率、相同时间预算下的箱底面积、P95 求解时间、结果稳定性、Windows/Linux 部署成本和许可证要求。

## 3. 为什么 CDR 和 PNG 必须分工

示例 PNG 同时包含字牌、尺寸线、说明文字、颜色和留白。仅靠图像分割无法可靠判断：

- 哪些像素属于实体外轮廓，哪些只是尺寸标注；
- `MINERVA GALLERY` 是整块灯牌、两个词组，还是 14 个独立字母；
- 玫瑰、叶片和 `Roses` 是一个不可拆成品还是多个可独立摆放的部件；
- 一个字母内部的孔洞能否放入另一件货物；
- PNG 缩放、抗锯齿和截图留白是否仍保持精确物理比例。

因此：

- **CDR 是尺寸和轮廓真值**；
- **PNG 是语义确认和视觉校验依据**；
- 如果 CDR 与 PNG 冲突，接口必须返回警告并要求确认，不能静默选一个结果。

当前样本目录中检测到 21 个 CDR，均为 `application/x-vnd.corel.zcf.draw.document+zip` 容器。当前开发机未检测到 CorelDRAW、Inkscape 或 UniConvertor 命令，因此还不能对这些文件做保真导出验证。

已确认的 golden case 对应关系：

- `BW2606-3081.cdr` 对应 `image-(6).png` 的 `RE2PECT`，属于需要识别并拆分字母后排样的案例。
- `AW2606-3064单_生产单.xlsx` 对应 `image-(22).png`；同目录的 `AW2606-3064单_生产文件.cdr` 缩略预览也与两个设计一致。生产单说明两个设计各 1 套、共 2 套，款式为普通背光字，但“可拆”字段为 `/`，因此不能只依靠生产单决定拆分粒度。
- `image-(15).png` 是整体圆形灯牌的反例：如果 CDR 存在包住全部图案的连续圆形实体底板，内部文字只是印刷、雕刻或贴图，则整个圆牌是一个装箱实体，不进行内部排样。

## 4. CDR 导入设计

### 4.1 首选路径：CorelDRAW Windows 转换工作节点

在安装了公司许可版本 CorelDRAW 的 Windows 节点上，以自动化脚本完成：

1. 打开 CDR；
2. 将文字转换为曲线，或验证文件已经转曲；
3. 保留页面、图层、组、对象名称和物理单位；
4. 导出 SVG，同时导出 PDF/PNG 供人工对照；
5. 写出 manifest，记录源文件 SHA-256、CorelDRAW 版本、页面尺寸、单位和对象映射；
6. 将 SVG + manifest 交给 FastAPI 几何管线。

转换工作节点与排样 API 解耦。这样 Linux/Docker 服务不需要直接安装桌面软件，也不会因为 CDR 版本变化而修改求解器。

### 4.2 备用路径

- 允许业务人员从 CorelDRAW 手工导出“SVG + PDF”，并上传到同一导入接口。
- 可以评估 `libcdr`/Inkscape 导入，但必须通过真实 CDR golden cases 的尺寸和轮廓对比后才能启用。
- UniConvertor 曾支持 CDR，但其支持范围和维护状态不足以作为现代 CDR 的唯一生产入口。

### 4.3 建议建立的 CDR 出图约定

自动化成功率取决于生产文件是否有机器可读语义。建议新增以下约定：

- 可装箱实体放在 `PACK_OUTLINE` 图层；标注、尺寸、渲染图分别放在其他图层。
- 每个可独立移动的实体是一个 group，命名为稳定的 `piece_id`。
- 一个 group 内的多条路径表示同一实体的外环和孔洞。
- 文字在交付前转曲。
- 重复件用数量属性表达，不靠复制到不可见页面表达。
- 文件使用 mm；若不是 mm，manifest 必须给出单位换算。
- 复合成品必须显式标记 `rigid_group=true`，避免算法错误拆开玫瑰和文字等固定结构。

对于不符合约定的历史文件，导入后进入一次“轮廓确认”页面：左侧显示 CDR/PNG，右侧显示检测出的实体，操作员可合并、拆分、删除标注并确认比例。确认结果保存为订单资产，后续求解不再重复识别。

### 4.4 自动判断整体、可拆和混合成品

系统判断的对象不是“图片里有几个图形”，而是“运输时有几个可以独立移动的刚性装箱实体”。一个订单允许同时包含整体灯牌、独立字母和多个复合 Logo 子组件。

#### 证据优先级

1. **物理加工轮廓和公共底板**：最高优先级。一个连续底板包住全部视觉元素时，默认是一个整体；不存在公共底板、每个字有独立字壳/底板时，默认拆成独立实体。
2. **CDR 图层、group 和对象名称**：`PACK_OUTLINE`、材料层、切割层和稳定 group 可直接定义刚性组件；标注层、效果图层和打印图层不得生成装箱实体。
3. **生产单工艺**：灯箱、整板 UV、喷绘布、公共背板等提高“一体”置信度；普通背光字、独立字壳和逐字出线提高“可拆”置信度。生产单中的“套”只表示销售/生产数量，不等于一个刚性实体。
4. **PNG/效果图**：只做一致性检查。视觉上接触、叠放或共享颜色不能证明物理连接。

#### 装箱实体图

导入阶段构建一个连接图：候选物理轮廓为节点，确定的刚性连接为边。公共底板、同一实体材料轮廓、结构桥接和明确的刚性 group 可以建立边；电线、视觉接触和相邻关系不能建立刚性边。图中的每个连通分量成为一个 `packing_unit`。

判定结果分为：

- `integrated`：一个整体实体，例如带完整圆形底板的 `image-(15).png`；
- `separable`：多个独立实体，例如 `RE2PECT` 的独立字牌；
- `mixed`：多个刚性子组件，例如“独立字母 + 一个不可再拆的花朵 Logo”；
- `needs_review`：CDR 轮廓与生产工艺冲突或置信度不足，必须确认后才能报价。

自动判定必须返回证据和置信度，例如“检测到一个公共圆形切割轮廓，覆盖 100% 内部视觉对象”。系统不得为了继续求解而把 `needs_review` 静默当成可拆或整体。

## 5. 几何标准化

规范化输出使用统一的内部模型：

```json
{
  "piece_id": "LETTER-R-01",
  "quantity": 1,
  "outer": [[0.0, 0.0], [120.0, 0.0], [118.0, 340.0]],
  "holes": [[[20.0, 40.0], [70.0, 40.0], [70.0, 120.0]]],
  "thickness_mm": 12.0,
  "rotation_mode": "continuous",
  "rotation_step_deg": 5,
  "allow_mirror": false,
  "rigid_group": true
}
```

处理规则：

1. 应用 SVG group transform，将坐标统一为 mm。
2. 将 Bézier 曲线和圆弧按可配置误差离散为线段；原始曲线继续保留用于最终 SVG。
3. 修复自交、重复点、极短边和错误环方向；无法无损修复时返回 422，不静默删形状。
4. 根据 group/layer 形成实体，保留孔洞。
5. 对实体外轮廓施加包装保护间距；如果要求两件货物之间净距为 `g`，碰撞几何可各自向外 buffer `g/2`。
6. 求解可使用简化轮廓提速，但最终验证必须使用未简化或更高精度轮廓。

默认禁止镜像。当前业务已确认允许非 90 度旋转和字母孔洞嵌套，因此引擎应支持连续角度或较细离散角度，并支持 `part_in_part=true`。最终可用角度仍需受朝向、易碎边、线缆和凸起约束；孔洞放置必须使用实体净空而不是只看正视图空白。镜像没有得到明确授权，继续保持关闭。

## 6. 求解模型

### 6.1 二维排样与多层组合

每一层仍是二维不规则排样，同层货物在 XY 投影上不能重叠。业务已确认不严格限制单层，因此外层优化器还需要决定实体分到哪一层。箱深不是求解器自动猜测的值，而应由包装工艺计算。

```text
inner_depth = max(piece_thickness)
            + top_padding
            + bottom_padding
            + protrusion_allowance
```

多层模式需按易碎性、厚度、重量、凸起、可承压面和线缆方向分层，再计算：

```text
inner_depth = sum(layer_max_thickness)
            + interlayer_padding * (layer_count - 1)
            + top_padding + bottom_padding
```

在厚度、上下缓冲、层间垫材和承压规则尚未确定前，系统可以生成几何候选，但不得把多层结果标为“可直接生产”。安全回退是单层方案。

### 6.2 搜索未知纸箱宽高

大多数开源 nesting 引擎需要给出容器宽度，而本需求要求同时寻找箱宽和箱高。外层编排器应执行：

1. 计算总膨胀面积、最大单件宽高和箱规上限，形成理论下界和可行区间。
2. 在候选箱宽上运行 strip-packing，求每个宽度对应的最小已用高度。
3. 先粗粒度搜索，再围绕最优宽度做细粒度搜索。
4. 对每个可行布局生成紧贴摆放结果的外接矩形。
5. 用原始轮廓验证后，按目标函数排序。

建议保留三个目标：

- `minimum_footprint`：先最小化 `width * height`，同面积时依次最小化最长边、周长和坐标离散程度，用作几何基线。
- `minimum_shipping_cost`：枚举可行箱规，接入现有 HK→SG 公开价引擎；先最小化可报价运输总价，同价时再最小化箱数、箱底面积和最长边。
- `minimum_total_cost`：当前需求的默认目标。最小化 `承运商报价 + 纸箱成本 + 保护材料/层间垫材 + 装箱人工 + 可配置风险成本`，同成本时优先箱数更少、体积更小和操作更简单的方案。

现有公开报价只覆盖运输费用。若纸箱、垫材和人工价表尚未提供，响应必须明确显示 `cost_scope="shipping_only"`，不能把它命名为完整总成本。

纸箱内尺寸和外尺寸必须分开返回。求解器输出内尺寸；外尺寸还需加纸板厚度、制造余量和取整规则。

### 6.3 可复现性和停止条件

- 请求必须记录 `solver_name`、版本、配置、time limit 和 RNG seed。
- 达到时间限制时返回当前最佳可行解，而不是请求失败。
- 返回面积下界和 `best_area / lower_bound`，明确结果质量，但不要把启发式结果标成“精确最优”。
- 求解进程应与 FastAPI 主进程隔离，支持超时终止和资源限制。

## 7. 独立验证器

任何求解结果在报价或展示前都必须经过与求解器独立的验证：

- 每个请求件数与输出件数一致；
- 所有旋转属于允许集合，且未发生镜像；
- 每个原始实体完全位于箱体内边距之内；
- 任意两个实体之间满足最小净距；
- 坐标变换后尺寸与 SVG 预览一致；
- 箱宽高为所有摆放实体的真实外接尺寸加边距，而不是求解器声明值；
- 物理比例与 CDR 页面单位或人工已知尺寸一致；
- PNG 与 SVG 的渲染叠加差异超过阈值时发出人工复核警告。

## 8. API 设计

建议拆成“导入确认”和“求解”两个阶段，以适配不规范的历史 CDR。

### 8.1 `POST /pack/irregular-2d/import`

`multipart/form-data`：

- `source_file`：CDR 或已导出的 SVG；
- `reference_image`：可选 PNG；
- `production_sheet`：可选 XLSX，用于提取款式、材料、数量、可拆、包装和安装工艺等辅助证据；
- `order_id`；
- `known_width_mm` / `known_height_mm`：可选比例校验值。

返回：

- `asset_id`；
- 文件哈希、页面、图层和单位；
- 自动检测出的 pieces；
- `assembly_classification`：`integrated` / `separable` / `mixed` / `needs_review`、置信度和逐条证据；
- `packing_units`：根据刚性连接图生成的实际可移动实体，而不是所有可见 SVG path；
- 每个 piece 的缩略图、边界框和警告；
- 一张带编号轮廓的 inspection SVG。

### 8.2 `POST /pack/irregular-2d/solve`

```json
{
  "order_id": "DW2606-3069",
  "asset_id": "asset_01J...",
  "pieces": [
    {"piece_id": "R", "quantity": 1, "thickness_mm": 12},
    {"piece_id": "E", "quantity": 2, "thickness_mm": 12}
  ],
  "packing": {
    "mode": "single_layer",
    "item_clearance_mm": 15,
    "edge_margin_mm": 20,
    "top_padding_mm": 20,
    "bottom_padding_mm": 20,
    "protrusion_allowance_mm": 5,
    "rotation_mode": "continuous",
    "rotation_step_deg": 5,
    "allow_mirror": false,
    "part_in_part": true,
    "max_inner_width_mm": 1500,
    "max_inner_height_mm": 1500,
    "carton_rounding_mm": 5
  },
  "objective": "minimum_total_cost",
  "time_limit_s": 30,
  "seed": 1
}
```

响应重点字段：

```json
{
  "status": "best_found",
  "cost_scope": "shipping_only",
  "carton": {
    "inner_dimensions_mm": [1300, 1310, 120],
    "outer_dimensions_mm": [1310, 1320, 130]
  },
  "utilization": 0.81,
  "lower_bound_area_mm2": 1230000,
  "placements": [
    {"piece_id": "R", "instance": 1, "x_mm": 20, "y_mm": 25, "rotation_deg": 90}
  ],
  "verification": {"valid": true, "minimum_clearance_mm": 15.0},
  "artifacts": {"svg": "/.../layout.svg", "html": "/.../layout.html"},
  "solver": {"name": "...", "version": "...", "seed": 1, "elapsed_ms": 8421}
}
```

求解若经常超过反向代理超时时间，再将接口平滑升级为异步 job（提交返回 202，轮询结果）；MVP 可先保持同步并设置严格 time limit。

## 9. 前端和结果图

现有 React/Vite 页面新增 `3D / 2D` 模式，而不是把二维结果伪装成 Mesh3d：

- 上传 CDR 和参考 PNG；
- 轮廓确认与实体合并/拆分；
- 输入厚度、数量、间距、边距和旋转规则；
- SVG 显示纸箱、实体编号、摆放顺序和净距；
- 支持缩放、单件高亮、原始朝向/排样朝向切换；
- 显示内尺寸、外尺寸、深度公式、利用率和“best found”状态；
- 下载/打印仓库操作图时包含件号、旋转角度、坐标原点和比例尺。

## 10. 交付阶段

### 阶段 A：数据和转换闭环

- 选取 10–30 份有代表性的 CDR、对应 PNG 和人工确认尺寸；
- 定义 `PACK_OUTLINE`/group 命名规范；
- 完成 CorelDRAW 导出 manifest；
- 完成 SVG 规范化、inspection SVG 和人工确认数据结构。

### 阶段 B：双引擎 POC

- 用相同轮廓、间距、旋转和时间预算测试 U-Nesting 与 Sparrow/Jagua；
- 构建独立验证器；
- 输出面积、耗时、稳定性和失败样本报告；
- 根据真实订单数据确定生产引擎，不在 POC 前绑定实现。

### 阶段 C：API 与二维可视化

- 实现 import/solve/viz 接口；
- React 增加二维工作流；
- 记录资产哈希、求解配置和结果，确保订单可追溯。

### 阶段 D：最低总成本、物流和多箱/多层

- 将二维箱规和深度接入现有公开运价；
- 增加最大箱规、多箱拆分、多层堆叠和 `minimum_total_cost`；
- 接入纸箱、保护材料和人工成本表；缺少这些成本时保持 `shipping_only` 标签。

## 11. 建议验收指标

- Golden CDR 转换成功率：100%；不支持的文件明确失败并给出回退方式。
- 已知尺寸误差：不高于 0.2% 且不高于 1 mm，最终阈值由生产工艺确认。
- 结果验证：零越界、零重叠、净距不小于配置值。
- 可复现：固定版本、配置和 seed 时输出一致。
- 性能建议值：50 个中等复杂轮廓在 30 秒内返回可行解；P95 目标待真实数据基准后确定。
- 质量建议值：同一时间预算下，生产引擎的中位箱底面积不劣于选定基线；所有回归样本不得退化超过约定阈值。

## 12. 已确认决策与待确认信息

已确认：

- 系统需要自动判断整体、可拆和混合成品；整体灯牌不拆，独立字牌拆分排样。
- 不严格限制单层，允许求解多层方案。
- 允许非 90 度旋转，也允许在具备真实净空时利用字母孔洞。
- 默认业务目标是最低总成本。
- 首批 golden case 已确定为 `BW2606-3081.cdr`、`AW2606-3064单_生产单.xlsx`/对应 CDR，以及整体圆牌 `image-(15).png`。

仍需确认：

1. 灯牌厚度、顶部/底部/层间缓冲、同层件间净距和箱边净距。
2. 多层时允许承压的产品类型、最大层数、隔板材料及其厚度/重量。
3. 是否已有纸箱、保护材料和人工成本表；否则第一版只能严格称为“最低运输成本”。
4. CDR 是否已有稳定图层/对象命名规范；公司是否能提供安装对应版本 CorelDRAW 的 Windows 转换节点。
5. 典型和最大订单件数、轮廓数量、可接受求解时间。
6. `image-(15).png` 对应的 CDR/生产单，用于验证整体圆牌自动判定。

## 13. 参考资料

- U-Nesting：<https://github.com/iyulab/u-nesting>
- irregular-object-packing：<https://github.com/MbBrainz/irregular-object-packing>
- Sparrow：<https://github.com/JeroenGar/sparrow>
- Jagua：<https://github.com/JeroenGar/jagua-rs>
- Deepnest/SVGnest 算法说明：<https://github.com/Jack000/Deepnest/blob/master/main/readme.md>
- Inkscape Wiki 的 UniConvertor 格式说明：<https://wiki.inkscape.org/wiki/Uniconvertor>
