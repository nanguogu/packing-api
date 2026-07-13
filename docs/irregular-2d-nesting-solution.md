# 不规则灯牌二维装箱方案

## 1. 结论

本需求应作为现有三维长方体装箱之外的一条独立管线实现，而不是把灯牌近似成外接矩形后继续调用 OR-Tools CP-SAT。

建议的生产流程是：

1. 以 CDR 中的矢量曲线和物理单位作为几何真值。
2. 将 CDR 转换为规范化 SVG；PNG 只用于人工对照、颜色/成品关系确认和结果预览，不用于决定生产尺寸。
3. 将每个实际需要装箱的灯牌转换为带孔多边形，并按包装间距向外膨胀。
4. 使用二维不规则排样引擎搜索每个灯牌的平移和旋转。
5. 在多个候选箱宽上求解最小箱高，再按箱底面积、最长边或物流总价选择方案。
6. 用原始高精度轮廓独立复验无重叠和不越界。
7. 输出纸箱内尺寸、建议外尺寸、深度、利用率、每件灯牌的坐标/角度，以及 SVG/HTML 摆放图。

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

## 5. 几何标准化

规范化输出使用统一的内部模型：

```json
{
  "piece_id": "LETTER-R-01",
  "quantity": 1,
  "outer": [[0.0, 0.0], [120.0, 0.0], [118.0, 340.0]],
  "holes": [[[20.0, 40.0], [70.0, 40.0], [70.0, 120.0]]],
  "thickness_mm": 12.0,
  "allowed_rotations_deg": [0, 90, 180, 270],
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

默认禁止镜像。旋转默认只允许 0/90/180/270 度；只有在包装工艺允许且实测显示有收益时才开放更细角度。字母内部孔洞默认不允许放置其他灯牌，确认不会损伤、卡住线缆或凸起后才开启 `part_in_part`。

## 6. 求解模型

### 6.1 单层二维假设

二维方案成立的前提是所有货物处于同一包装层且在 XY 投影上不能重叠。箱深不是求解器自动猜测的值，而应由包装工艺计算：

```text
inner_depth = max(piece_thickness)
            + top_padding
            + bottom_padding
            + protrusion_allowance
```

如果允许多层堆叠，则问题变为“二维排样 + 分层”，需先按易碎性、厚度和重量分层，再计算：

```text
inner_depth = sum(layer_max_thickness)
            + interlayer_padding * (layer_count - 1)
            + top_padding + bottom_padding
```

这不能与单层模式混为一谈。

### 6.2 搜索未知纸箱宽高

大多数开源 nesting 引擎需要给出容器宽度，而本需求要求同时寻找箱宽和箱高。外层编排器应执行：

1. 计算总膨胀面积、最大单件宽高和箱规上限，形成理论下界和可行区间。
2. 在候选箱宽上运行 strip-packing，求每个宽度对应的最小已用高度。
3. 先粗粒度搜索，再围绕最优宽度做细粒度搜索。
4. 对每个可行布局生成紧贴摆放结果的外接矩形。
5. 用原始轮廓验证后，按目标函数排序。

建议保留两种目标：

- `minimum_footprint`：先最小化 `width * height`，同面积时依次最小化最长边、周长和坐标离散程度。这是当前需求的默认目标。
- `minimum_shipping_cost`：枚举可行箱规，接入现有 HK→SG 公开价引擎；先最小化可报价运输总价，同价时再最小化箱数、箱底面积和最长边。

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
- `order_id`；
- `known_width_mm` / `known_height_mm`：可选比例校验值。

返回：

- `asset_id`；
- 文件哈希、页面、图层和单位；
- 自动检测出的 pieces；
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
    "allowed_rotations_deg": [0, 90, 180, 270],
    "allow_mirror": false,
    "part_in_part": false,
    "max_inner_width_mm": 1500,
    "max_inner_height_mm": 1500,
    "carton_rounding_mm": 5
  },
  "objective": "minimum_footprint",
  "time_limit_s": 30,
  "seed": 1
}
```

响应重点字段：

```json
{
  "status": "best_found",
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

### 阶段 D：物流和多箱/多层

- 将二维箱规和深度接入现有公开运价；
- 增加最大箱规、多箱拆分和 `minimum_shipping_cost`；
- 业务确认后再增加多层堆叠。

## 11. 建议验收指标

- Golden CDR 转换成功率：100%；不支持的文件明确失败并给出回退方式。
- 已知尺寸误差：不高于 0.2% 且不高于 1 mm，最终阈值由生产工艺确认。
- 结果验证：零越界、零重叠、净距不小于配置值。
- 可复现：固定版本、配置和 seed 时输出一致。
- 性能建议值：50 个中等复杂轮廓在 30 秒内返回可行解；P95 目标待真实数据基准后确定。
- 质量建议值：同一时间预算下，生产引擎的中位箱底面积不劣于选定基线；所有回归样本不得退化超过约定阈值。

## 12. 实施前需要业务确认的信息

1. 每个字母通常是独立货物，还是一个单词/Logo 必须保持为不可拆整体？
2. 灯牌是否严格单层摆放；如果可多层，层间需要什么保护材料和承重限制？
3. 两件灯牌之间、灯牌到箱边分别要求多少净距？箱规应输出内尺寸还是外尺寸？
4. 是否允许任意角度旋转、只允许 90 度旋转；是否一律禁止镜像？
5. 字母孔洞中是否允许放入其他灯牌？
6. CDR 中是否已有稳定图层/对象命名规范；公司是否能提供一台安装了对应版本 CorelDRAW 的 Windows 节点？
7. 典型和最大订单件数、轮廓数量、期望求解时间是多少？
8. 默认目标是最小箱底面积、最小最长边、最小体积，还是包含运费后的最低总成本？
9. 请指定一个 CDR 与 `image-(6).png` 或 `image-(22).png` 的准确对应关系，作为第一个端到端 golden case。

## 13. 参考资料

- U-Nesting：<https://github.com/iyulab/u-nesting>
- irregular-object-packing：<https://github.com/MbBrainz/irregular-object-packing>
- Sparrow：<https://github.com/JeroenGar/sparrow>
- Jagua：<https://github.com/JeroenGar/jagua-rs>
- Deepnest/SVGnest 算法说明：<https://github.com/Jack000/Deepnest/blob/master/main/readme.md>
- Inkscape Wiki 的 UniConvertor 格式说明：<https://wiki.inkscape.org/wiki/Uniconvertor>
