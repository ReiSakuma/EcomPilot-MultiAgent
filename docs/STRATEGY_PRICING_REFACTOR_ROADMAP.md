# EcomPilot Strategy 定价与候选方案重构技术路线

## 1. 文档目的

本文档用于指导 v50 之后对 Strategy Agent 的稳定化重构，集中解决以下问题：

1. 用户心理预期售价明显偏离市场价格时，系统仍继续生成商品文案和促销策略，既浪费模型调用，也容易给出不合理方案。
2. Strategy Agent 同时承担证据选择、促销创意、金额计算、库存校验和长文生成，职责过重。
3. `discount`、`selected_discount` 等字段没有明确区分“优惠金额”和“折扣比例”，模型容易把“10 元券”写成“九折”或“10% 优惠”。
4. 模型会在文案中重新计算到手价、毛利率和库存，导致模型叙述与工具结果不一致。
5. Review 发现数字错误后会触发整段 Strategy 重新生成，增加超时、长度截断和二次出错的概率。
6. 当前页面没有稳定展示“售价相对市场偏高或偏低”的经营提醒。

本次重构不增加更多常驻子 Agent。Strategy Agent 仍是一个业务 Agent，但内部改成“模型负责提出和选择策略，程序负责计算和裁决”的分层结构。

## 2. 核心结论

### 2.1 市场价格偏离阈值

消费电子等价格透明、用户容易比价的普通商品，默认采用：

```text
市场接受区间 = 清洗后的参考中心价格 x [85%, 115%]
```

即默认允许用户心理预期售价相对核心可比组的市场参考中心上下浮动 `15%`。参考中心在核心可比数据稳定时采用清洗后平均价，否则采用清洗后中位价。

`15%` 是 EcomPilot 的初始产品策略参数，不是行业强制标准。选择它的原因是：

- `10%` 对新品、品牌溢价和功能差异化商品过于严格。
- `20%` 至 `30%` 对普通消费电子又过于宽松，容易放过明显脱离市场的输入。
- `15%` 可以先识别值得商家重新确认的异常价格，同时保留一定定位空间。

系统不能直接对整个品类的原始价格求平均。应先完成规格归一化、异常值清洗和可比性分层，再从 `core_comparable` 计算：

- `core_median_price`：核心可比组清洗后中位价，始终作为稳健基准。
- `core_mean_price`：核心可比组清洗后平均价，用来表达同层商品的整体价格水平。
- `core_reference_price`：价格门控真正采用的中心价格。

当核心可比样本充足，且核心平均价和中位价相差不超过 `5%` 时，可以使用核心平均价作为 `core_reference_price`；否则继续使用核心中位价。这样既满足“剔除脏数据后重新计算平均值”的经营直觉，也不会在同层市场价格仍然偏斜时被平均值误导。

阈值必须可配置，不能散落在 Prompt 或前端代码中：

| 定价画像 | 默认阈值 | 适用情况 |
| --- | ---: | --- |
| `commodity` | +/-10% | 同质化高、价格非常透明的标品 |
| `standard` | +/-15% | 无线耳机等普通消费电子，项目默认值 |
| `differentiated` | +/-25% | 有明确品牌、专利、材质、服务或独占功能证据的商品 |

模型可以建议某个定价画像，但无权自行扩大阈值。画像变更必须有已确认商品事实或用户明确确认。

### 2.2 偏离阈值不是“违法”，而是“暂停并确认”

售价超出市场接受区间时，系统不应把请求标记成技术失败，也不应直接否定用户，而应进入：

```text
waiting_user / price_confirmation_required
```

此时系统需要：

1. 暂停 Listing、Strategy、Review 和店铺写入。
2. 告诉用户其售价比核心可比参考价格高或低多少，并同时展示核心平均价和中位价。
3. 展示市场样本量、数据时间和证据可信度。
4. 给出建议基础售价区间。
5. 让用户选择修改售价、保留售价并说明定位依据，或仅查看市场分析。

用户确认高价定位后可以继续，但必须记录 `pricing_override` 审计信息。系统不能悄悄修改用户确认的基础售价。

### 2.3 候选策略不是写死的五档优惠

程序不得对所有商品固定生成 `0/5/10/15/20 元` 五档方案。

正确职责划分是：

- 大模型根据商品类别、目标人群、市场证据、历史活动和运营目标，动态提出 2 至 4 个候选策略。
- 模型必须使用明确的促销类型和单位，例如“10 元券”或“九折”，不能只返回含义模糊的 `discount=10`。
- 确定性工具将每个候选换算成准确到手价、毛利率、预计投入和库存结果。
- 不合格候选被单独淘汰，不导致整个任务失败。
- 大模型只能从工具判定合格的候选中选择最终方案。
- 最终页面中的所有数字由程序模板根据工具结果渲染，模型不再手算或重写数字。

### 2.4 价格可信不等于商品可比

异常值检测只回答“这条价格数据是否可信”，不能回答“这件商品是否适合参与当前商品的定价”。例如，699 元的高端降噪耳机可能是真实商品，但它不一定适合参与普通游戏耳机的核心参考价计算。

系统必须同时保留三个市场口径：

| 证据层 | 定义 | 是否参与价格门控 | 主要用途 |
| --- | --- | --- | --- |
| `core_comparable` | 类目、形态、核心功能、目标人群、渠道和价格层与目标商品接近 | 是 | 计算核心均值、中位价、接受区间和目标售价偏离 |
| `adjacent_tier` | 同类目但属于相邻入门或高端档次，仍有经营参考价值 | 否 | 解释升级配置、品牌、服务或降配后能够支持的价格 |
| `full_valid_market` | 已排除脏数据后的整个有效品类市场 | 否 | 展示品类全局价格范围和市场分层，不用于硬性否决 |

因此，系统不得只输出一个含义模糊的“市场均价”。页面和 Artifact 应明确展示：

- `core_reference_price`：本次价格门控使用的核心可比参考价。
- `core_price_band`：核心可比商品价格区间。
- `adjacent_tier_bands`：相邻档次价格区间及其差异化依据。
- `full_market_band`：整个有效品类的价格范围。

极端但合理的商品必须保留在原始市场证据和全市场统计中。只有当它与目标商品属于同一可比层时，才进入核心参考价计算；如果属于不同品牌、规格或服务档次，则进入相邻档次证据。即使它进入核心可比组，只要均值和中位价差异过大，价格门控仍应回退到更稳健的中位价。

## 3. 参考依据与边界

Shopify 的官方定价指南把成本加成、价值定价和竞品定价列为不同策略，并明确允许有真实质量差异的商品高于市场平均价格。Stripe 的定价指南同样指出，竞争定价可以低于、等于或高于竞品，但高价需要价值、差异化或品牌定位支撑。因此，本项目不能把“高于市场价”直接当成错误，只能把明显偏离设置为需确认的经营风险。

参考资料：

- Shopify，Pricing your products：https://help.shopify.com/en/manual/products/details/product-pricing/determine-pricing
- Stripe，Competitors' pricing strategies：https://stripe.com/resources/more/competitors-pricing-strategies
- Stripe，Strategy of pricing：https://stripe.com/resources/more/strategy-of-pricing

当前项目已经为无线/蓝牙耳机和机械键盘分别提供 100 个合成商品样本及 200 条合成评论。部分细分人群查询仍可能只命中少量可比商品，因此：

- 页面必须明确标记 `synthetic_seed`、`degraded` 或真实数据来源。
- 总样本量不能代替可比样本量；价格门控必须使用当前规格、人群和价格层清洗后实际保留的样本数。
- 低样本结果可以触发“请确认”提醒，但不能冒充统计结论或平台规则。
- 接入真实业务后，应按品类、渠道和价格带重新校准阈值。

v51 已把两组数据固定为每类 94 条常规商品、2 条明显脏数据和 4 条极端但合理商品。特殊样本能够被 log-MAD 或 IQR 标记，并带有仅供离线测试使用的期望标签；运行时加载器会剥离这些答案。v51 只建立验收输入，不负责删除异常值，异常清洗和可比性分层分别由 v52、v53 完成。

## 4. 新的主流程

建议把商品上架流程调整为：

```text
用户输入
  -> LLM Semantic Compiler（语义理解并生成结构化字段）
  -> Deterministic Preflight（成本、库存、毛利、宣传、注入攻击检查）
  -> Market Agent（获取规范类目下的候选市场证据）
  -> Market Evidence Cleaner（单位归一化、脏数据识别和稳健统计）
  -> Comparable Market Classifier（形成核心可比、相邻档次和全市场三层证据）
  -> Market Price Gate（市场价格偏离检查）
      -> 价格需确认：暂停并向用户提问
      -> 价格可接受：继续
  -> Listing Agent（生成商品页面内容）
  -> Strategy Agent / Candidate Proposer（动态提出候选策略）
  -> Candidate Evaluation Tool（统一计算与过滤）
  -> Strategy Agent / Candidate Selector（只选择合格候选）
  -> Deterministic Strategy Renderer（生成带准确数字的展示内容）
  -> Review Agent（事实、宣传和跨 Artifact 语义审核）
  -> 用户确认写入
  -> Browser Agent / Seller Center
```

这里有两次不同性质的检查：

- `Deterministic Preflight` 是执行前安全检查，必须在市场调研和方案生成之前。
- `Review Agent` 是输出质量检查，检查 Listing 和 Strategy 最终产物，不应代替前置安全检查。

## 5. Market Price Gate 设计

### 5.1 市场价格清洗与可比性分层

市场样本不能直接进入平均值计算。清洗过程必须是确定性的概率统计程序，不调用大模型。

#### 5.1.1 先保证商品可比

统计异常不等于业务异常。一个 999 元耳机可能是错误数据，也可能是真实的高端品牌款。因此，异常检测之前先做以下归一化：

1. 统一币种、含税口径和是否包含运费。
2. 统一计价单位，例如单只、单副、两件套和组合装不能直接比较。
3. 区分新品、二手、翻新和不同成色。
4. 按类目、主要规格、品牌层级和渠道建立可比商品组。
5. 对同一个 SKU、同一渠道的重复采集记录去重。
6. 过滤非正价格、缺失价格和单位无法换算的记录。

以上步骤使用商品结构化字段、类目映射表和单位换算规则完成，不让模型凭感觉判断。

这里的“建立可比商品组”不是简单按照价格筛选。不得先选择目标售价附近的商品，再用它们证明目标售价合理，否则会形成选择偏差。第一轮核心匹配优先使用以下顺序：

1. 规范类目和产品形态。
2. 用户已经确认的核心功能和规格。
3. 目标人群与主要使用场景。
4. 新旧状态、单品或套装、渠道和品牌层级。
5. 最后才把价格层作为结果解释字段，而不是最初筛选条件。

目标价格附近的商品可以在价格门控之后形成 `price_neighborhood` 证据，用于研究该价位常见的营销方式和溢价支撑，但不能反向参与核心参考价计算。

#### 5.1.2 异常值算法

价格是正数，而且经常呈右偏分布，因此先对价格取自然对数：

```text
y_i = ln(price_i)
```

然后使用 `MAD`，即 Median Absolute Deviation，中文为“中位数绝对偏差”：

```text
m = median(y)
MAD = median(abs(y_i - m))
modified_z_i = 0.6745 x (y_i - m) / MAD
```

当 `abs(modified_z_i) > 3.5` 时，将该价格标记为统计异常候选。MAD 以中位数为中心，不容易被极端价格本身拖偏。

同时使用 `IQR`，即 Interquartile Range，中文为“四分位距”进行复核：

```text
IQR = Q3 - Q1
lower_fence = Q1 - 1.5 x IQR
upper_fence = Q3 + 1.5 x IQR
```

建议采用保守裁决：

- 同时被 MAD 和 IQR 标记，且不存在不同规格、品牌层级或组合装解释时，才从参考价格计算中排除。
- 只被一种算法标记时，记录为 `suspicious`，仍保留在样本中并降低证据置信度。
- `MAD=0` 时只使用 IQR；`IQR=0` 时不执行自动剔除，只记录价格高度集中。
- 原始样本少于 5 个时不自动剔除任何统计异常值，只做数据质量提醒。

不要使用普通 Z-score 作为首选。Z-score 依赖均值和标准差，而均值和标准差正是最容易被极端价格影响的两个量。

#### 5.1.3 防止误删真实高端商品

统计清洗之前必须先分组。如果样本同时包含入门款、普通款和高端款，应按可解释字段拆成不同价格层，而不是把高端层直接当异常值删除。

第一版不需要让大模型判断价格层，可使用以下确定性字段：

- 规范类目和产品形态。
- 品牌层级或自营品牌标记。
- 核心规格区间。
- 新旧状态。
- 单品、套装和赠品组合。
- 销售渠道。

如果分布存在明显多峰，但缺少足够字段解释不同价格层，返回 `distribution_ambiguous`，只展示分层或宽区间，不执行强价格门控。

每条被统计方法标记的样本都必须继续经过业务解释判断：

- 有明确高端品牌、额外功能、材质、认证、售后或组合装依据：标记为 `explainable_extreme`，保留数据。
- 有明确清仓、临期活动、翻新或二手依据：保留记录，但不得冒充常规新品售价。
- 单位错误、录入错误、重复采集、来源不可验证且没有业务解释：标记为 `dirty_outlier`，从统计计算中排除。
- 证据不足，无法判断：标记为 `suspicious`，不自动删除，并降低证据质量。

“保留数据”不表示“一定参与核心均价”。`explainable_extreme` 还必须经过可比性分类：同层商品进入 `core_comparable`，不同层商品进入 `adjacent_tier`，并始终保留在 `full_valid_market`。

#### 5.1.4 清洗后统计量

清洗和分层后输出：

```json
{
  "raw_sample_count": 15,
  "valid_sample_count": 14,
  "core_comparable_count": 12,
  "adjacent_tier_count": 2,
  "excluded_sample_count": 1,
  "suspicious_sample_count": 0,
  "raw_price_band": [79, 9999],
  "core_price_band": [199, 239],
  "adjacent_tier_bands": {
    "premium": [329, 699]
  },
  "full_market_band": [79, 699],
  "core_median_price": 219,
  "core_mean_price": 218.75,
  "mean_median_gap_rate": 0.0011,
  "core_reference_price": 218.75,
  "reference_method": "core_cleaned_mean",
  "distribution_status": "stable",
  "excluded_samples": [
    {
      "sample_id": "competitor_13",
      "price": 9999,
      "reason_codes": ["mad_extreme", "iqr_extreme"]
    }
  ]
}
```

原始记录不得物理删除。每个被排除样本的价格、来源、算法分数和排除原因都要写入 `ResearchEvidence` 和 Run Bundle，方便复核。

#### 5.1.5 参考中心选择

建议使用以下确定性规则：

```text
if core_comparable_count >= 10
   and evidence_quality == "high"
   and mean_median_gap_rate <= 0.05
   and distribution_status == "stable":
    core_reference_price = core_mean_price
    reference_method = "core_cleaned_mean"
else:
    core_reference_price = core_median_price
    reference_method = "core_cleaned_median"
```

清洗后至少保留原样本的 `70%`，且不少于 5 个样本。若不满足，说明清洗动作过强或样本本身混乱，应降级为 `advisory_only`，不能用剩余少量数据形成硬门控。

上述参考中心只能从 `core_comparable` 集合计算。`adjacent_tier` 和 `full_valid_market` 可以分别计算描述性均值、中位价和区间，但这些数字只能用于解释，不能传给 `Market Price Gate` 作为裁决中心。

#### 5.1.6 合成样本与异常识别验收数据

无线耳机和机械键盘每个品类继续保持 100 个商品样本，建议采用以下固定构成：

| 样本类型 | 数量 | 预期行为 |
| --- | ---: | --- |
| 常规价格层商品 | 94 | 参与对应可比层统计 |
| 明显脏数据 | 2 | 被双算法或质量规则识别，并从统计计算排除 |
| 极端但合理商品 | 4 | 保留；根据规格和档次进入核心可比或相邻档次 |

特殊样本必须包含可机器验证的测试元数据，例如：

```json
{
  "price": 9999,
  "test_case": "dirty_outlier",
  "expected_statistical_flag": true,
  "expected_excluded": true,
  "expected_market_layer": "excluded",
  "outlier_reason": "疑似价格录入错误"
}
```

```json
{
  "price": 699,
  "tier": "premium",
  "confirmed_features": ["主动降噪", "高端品牌", "两年保修"],
  "test_case": "explainable_extreme",
  "expected_statistical_flag": true,
  "expected_excluded": false,
  "expected_market_layer": "adjacent_tier",
  "price_explanation": "配置、品牌与售后能够解释高价"
}
```

测试元数据只用于离线验收，不能作为线上算法的输入答案。线上分类仍必须根据真实字段和统计结果作出，以避免测试标签泄漏。

### 5.2 输入协议

新增 `MarketPriceAssessmentInput`：

```json
{
  "target_price": 300,
  "cost": 95,
  "min_margin_rate": 0.4,
  "category": "无线耳机",
  "pricing_profile": "standard",
  "market": {
    "raw_sample_count": 13,
    "valid_sample_count": 14,
    "core_comparable_count": 12,
    "adjacent_tier_count": 2,
    "core_median_price": 219,
    "core_mean_price": 218.75,
    "core_reference_price": 218.75,
    "reference_method": "core_cleaned_mean",
    "core_price_band": [199, 239],
    "adjacent_tier_bands": {
      "premium": [329, 699]
    },
    "full_market_band": [79, 699],
    "distribution_status": "stable",
    "source_type": "market_database",
    "freshness_days": 3,
    "confidence": 0.86
  }
}
```

### 5.3 确定性计算

```text
deviation_rate = (target_price - core_reference_price) / core_reference_price
acceptance_low = core_reference_price x (1 - threshold)
acceptance_high = core_reference_price x (1 + threshold)
margin_floor = cost / (1 - min_margin_rate)
suggested_low = max(acceptance_low, margin_floor)
suggested_high = acceptance_high
```

如果 `suggested_low > suggested_high`，说明“市场价格、成本和最低毛利要求互相冲突”。系统不能编造建议区间，应要求用户调整成本、最低毛利率或商品定位。

价格应使用普通人民币价格点进行友好取整，但原始计算值必须保留在审计记录中。例如 186.15 至 251.85 元，可以展示为约 189 至 249 元；具体取整策略由配置管理，不交给模型随意处理。

### 5.4 状态协议

新增 `MarketPriceAssessment`：

```json
{
  "status": "confirmation_required",
  "core_reference_price": 218.75,
  "reference_method": "core_cleaned_mean",
  "core_mean_price": 218.75,
  "core_median_price": 219,
  "target_price": 300,
  "deviation_rate": 0.3714,
  "threshold_rate": 0.15,
  "acceptance_band": [185.94, 251.56],
  "suggested_price_range": [189, 249],
  "position": "above_market",
  "evidence_quality": "high",
  "reason_code": "target_price_above_market_band"
}
```

`status` 只允许：

- `passed`：位于接受区间，可继续。
- `confirmation_required`：超出区间，暂停等待用户。
- `advisory_only`：证据不足，只提示，不作硬判断。
- `unavailable`：没有可用市场价格证据，流程可以降级继续，但必须显示数据缺失。

### 5.5 证据质量

初始建议：

| 质量 | 条件 | 行为 |
| --- | --- | --- |
| `high` | 清洗后可比样本不少于 10 个、数据较新、类别匹配、分布稳定 | 使用阈值并暂停确认 |
| `medium` | 清洗后 5 至 9 个样本，或数据较旧 | 使用阈值并暂停确认，同时提示可信度有限 |
| `low` | 少于 5 个样本或类别映射较弱 | `advisory_only`，优先补充市场证据；不把结果称为市场定论 |

这些也是项目初始参数，应放入配置并通过真实数据校准。

### 5.6 用户交互

价格偏高时回答示例：

> 你的计划售价为 300 元。系统在 13 个可比样本中排除了 1 个统计极端价格，清洗后平均价约为 219 元，你的售价高出约 37%。按普通无线耳机默认的 +/-15% 参考范围，建议基础售价约为 189 至 249 元。你可以把售价调整到该区间；如果商品有已确认的品牌、材质、售后或独占功能优势，也可以保留 300 元并说明高价依据。

页面提供三个明确操作：

1. `采用建议价格`：用户选择或输入新价格，创建新状态版本后继续。
2. `保留原价并说明依据`：记录用户确认和差异化证据，再重新评估定价画像。
3. `只查看市场分析`：终止写入路径，保留研究结果。

价格偏低也要提醒，因为它可能导致低毛利、品牌定位损害或后续促销空间不足。

## 6. Strategy Agent 内部重构

### 6.1 保留一个 Agent，拆分内部阶段

Strategy Agent 不再一次返回完整长文，而是按以下阶段工作：

1. `Evidence Selection`：ReAct 自主选择最多两个真正有用的可选证据工具。
2. `Candidate Proposal`：模型动态提出 2 至 4 个候选策略。
3. `Candidate Evaluation`：程序统一计算和校验每个候选。
4. `Candidate Selection`：模型只能从 `eligible=true` 的候选中选一个。
5. `Deterministic Rendering`：程序把可信数字填入固定的业务表达结构。

这不是增加五个 Agent，而是让同一个 Strategy Agent 内部具有清晰阶段和数据协议。

### 6.2 动态候选策略

模型根据以下上下文动态提出候选：

- 用户确认的售价、成本、库存、最低毛利率。
- 商品类别、目标人群和运营目标。
- 市场价格位置和价格偏离提醒。
- 已确认商品功能，不能使用未确认宣传。
- 可选的需求预测、历史活动和竞品价格变化证据。
- 店铺已有活动、渠道限制和优惠政策。

不同商品可以得到不同候选。例如：

- 游戏耳机：限量首发券、游戏场景组合包、分阶段缩券。
- 高频消耗品：多件折扣、订购优惠、满件减。
- 高客单商品：分期、赠品、延保，而不一定直接降价。
- 高库存尾货：清仓折扣或阶梯降价。

如果某种策略需要未知成本，例如赠品成本或组合商品成本，候选应被标记为 `needs_data`，而不是假设成本为零。

## 7. 消除优惠单位歧义

废弃含义模糊的单个 `selected_discount: float` 作为模型协议。改用 Pydantic 可辨识联合类型 `Discriminated Union`，中文可理解为“先明确优惠类型，再使用该类型专属字段”。

### 7.1 无优惠

```json
{
  "promotion_type": "no_discount"
}
```

### 7.2 固定金额优惠券

```json
{
  "promotion_type": "fixed_amount_coupon",
  "amount_yuan": 10
}
```

### 7.3 百分比折扣

```json
{
  "promotion_type": "percentage_discount",
  "percent_off": 10
}
```

### 7.4 组合或赠品策略

```json
{
  "promotion_type": "bundle_offer",
  "bundle_sku_ids": ["sku_case_01"],
  "bundle_price": 319,
  "known_incremental_cost": 18
}
```

所有 Schema 使用 `extra="forbid"`。例如 `percentage_discount` 只能包含 `percent_off`，不能同时出现 `amount_yuan`，从协议层阻止“10 元”和“10%”混淆。

## 8. 候选方案协议

### 8.1 模型输出

新增 `StrategyCandidateProposal`：

```json
{
  "candidate_id": "candidate_launch_coupon",
  "objective": "降低首次购买门槛并保留后续调价空间",
  "promotion": {
    "promotion_type": "fixed_amount_coupon",
    "amount_yuan": 10
  },
  "planned_units": 300,
  "duration_days": 14,
  "evidence_refs": ["market_01", "campaign_02"],
  "assumptions": []
}
```

模型可以提出金额和持续时间，但这些数字只是“待验证提案”，不是可信业务结果。

### 8.2 工具评估输出

新增 `EvaluatedStrategyCandidate`：

```json
{
  "candidate_id": "candidate_launch_coupon",
  "promotion": {
    "promotion_type": "fixed_amount_coupon",
    "amount_yuan": 10
  },
  "base_price": 239,
  "discount_amount_yuan": 10,
  "net_price": 229,
  "cost": 95,
  "margin_yuan": 134,
  "margin_rate": 0.5852,
  "planned_units": 300,
  "inventory_remaining": 500,
  "eligible": true,
  "rejection_reasons": [],
  "evidence_refs": ["tool_margin_01", "tool_inventory_01"]
}
```

程序统一负责：

- 将百分比折扣换算成金额。
- 计算到手价、单位毛利和毛利率。
- 检查最低毛利率。
- 检查计划投入量和库存。
- 检查店铺优惠上限和活动冲突。
- 检查基础售价是否经过市场价格确认。
- 对组合、赠品和满减计算已知增量成本。

### 8.3 模型最终选择

模型最后只返回：

```json
{
  "selected_candidate_id": "candidate_launch_coupon",
  "selection_reason": "兼顾冷启动转化、毛利底线和后续调价空间",
  "caveats": ["市场证据为演示样本"]
}
```

模型不再重复返回到手价、毛利率和库存。若选择不存在或 `eligible=false` 的候选，协议校验直接拒绝该选择并允许一次受控重选。

## 9. 数字所有权

每个业务数字必须只有一个权威来源：

| 数字 | 权威来源 | 模型权限 |
| --- | --- | --- |
| 基础售价、成本、库存、最低毛利率 | 用户确认字段 | 只能引用，不能修改 |
| 市场中位价、价格带、样本量 | Market Artifact | 只能解释，不能改写 |
| 优惠提案金额或比例 | Strategy Candidate | 可以提出，必须明确单位 |
| 到手价、毛利额、毛利率 | Pricing Tool | 不得自行计算 |
| 计划投入和剩余库存 | Inventory Tool | 可以提出投入量，不能自行确认结果 |
| 最终页面中的数字句子 | Deterministic Renderer | 模型不得重写 |

例如最终显示文字应由模板生成：

```text
基础售价 {base_price} 元，使用 {amount_yuan} 元首发券，
预计到手价 {net_price} 元，单位毛利 {margin_yuan} 元，
预计毛利率 {margin_rate_percent}%。
```

这样不再依赖正则表达式事后把“270 元”替换为“290 元”。

## 10. 市场高价提醒的展示要求

即使用户确认保留高价并继续，最终回答和 Strategy 页面也必须持续展示定价位置：

- 用户目标售价。
- 市场中位价和参考区间。
- 高于或低于中位价的百分比。
- 用户保留该价格的确认状态。
- 支撑高价的已确认差异化事实。
- 数据来源、样本量和可信度。

这是一项经营提醒，不应被后续促销文案覆盖。

如果用户售价 300 元、市场中位价 219 元，系统应明确说明“高约 37%”，而不是只展示 300 元和高毛利率。

## 11. Review 与修复策略

Review 继续负责：

- Listing 是否包含未确认功能、虚假宣传或禁用表达。
- Strategy 的选择理由是否与候选证据一致。
- Listing、Strategy 和 Market 三者的定位是否语义一致。
- 最终文本是否引用了正确的工具数字。

Review 不再要求 Strategy Agent 重算数字。处理方式改为：

| 问题 | 修复方式 |
| --- | --- |
| 数字展示错误 | 重新运行确定性 Renderer，不调用模型 |
| 单个候选不合格 | 淘汰该候选，保留其他候选 |
| 选择了不合格候选 | 允许一次受控重选 |
| 宣传表达错误 | 仅重写对应文案字段 |
| 所有候选都不合格 | 一次约束化候选修复；仍失败则向用户澄清 |
| 市场价格未经确认 | 返回 `waiting_user`，不进入执行 |

禁止因为一个候选失败就把整个任务标记为技术失败。

## 12. 超时与调用预算

建议为 Strategy 内部阶段设置独立预算，而不是让一次全局超时掩盖具体问题：

- 证据选择：最多 2 个可选工具。
- 候选生成：最多 1 次正常调用 + 1 次 JSON 修复。
- 候选评估：本地确定性工具，不调用模型。
- 候选选择：最多 1 次正常调用 + 1 次受控重选。
- 数字渲染：本地确定性代码。
- 数字不一致：不得触发整段 Strategy 长文重生成。

超时错误必须指出实际阶段，例如 `strategy_candidate_selection_timeout`，不能统一显示为模糊的“节点超时”。

## 13. 配置建议

新增集中配置：

```text
ECOMPILOT_MARKET_PRICE_DEVIATION_STANDARD=0.15
ECOMPILOT_MARKET_PRICE_DEVIATION_COMMODITY=0.10
ECOMPILOT_MARKET_PRICE_DEVIATION_DIFFERENTIATED=0.25
ECOMPILOT_MARKET_PRICE_MIN_HIGH_CONFIDENCE_SAMPLES=10
ECOMPILOT_MARKET_PRICE_MIN_MEDIUM_CONFIDENCE_SAMPLES=5
ECOMPILOT_MARKET_OUTLIER_MAD_Z_THRESHOLD=3.5
ECOMPILOT_MARKET_OUTLIER_IQR_MULTIPLIER=1.5
ECOMPILOT_MARKET_MIN_RETAINED_RATIO=0.70
ECOMPILOT_MARKET_MEAN_MEDIAN_MAX_GAP=0.05
ECOMPILOT_STRATEGY_MIN_CANDIDATES=2
ECOMPILOT_STRATEGY_MAX_CANDIDATES=4
ECOMPILOT_STRATEGY_MAX_RESELECTIONS=1
```

配置应进入运行快照和 Run Bundle，便于解释一次运行为什么被暂停或放行。

## 14. 代码修改位置

建议按现有项目边界修改：

| 模块 | 修改内容 |
| --- | --- |
| `app/config.py` | 增加价格偏离、异常值和候选数量配置 |
| `app/orchestration/artifacts.py` | 扩展 ResearchEvidence 清洗审计和三层市场证据，增加市场价格评估、候选和最终选择 Artifact |
| `app/model/contracts.py` | 用可辨识联合类型替换模糊 `selected_discount` 协议 |
| `app/tools/contracts.py` | 增加候选评估输入输出 Schema |
| `app/tools/market_statistics.py` | 新增可比样本归一化、log-MAD、IQR、清洗后均值和参考中心选择 |
| `scripts/generate_market_samples.py` | 生成常规、脏数据和极端但合理样本，并写入离线期望标签 |
| `data/products/*.json` | 保留原始市场事实及合成测试元数据，不物理删除离群记录 |
| `app/tools/pricing_tools.py` | 增加市场偏离、价格建议和促销归一化计算 |
| `app/tools/strategy_evidence_tools.py` | 从固定优惠数组模拟改为评估模型动态候选 |
| `app/agents/strategy.py` | 重构为证据选择、候选生成、候选选择三个模型阶段 |
| `app/copilot/graph.py` | 在 Market 后增加清洗、可比性分层、`market_price_gate` 和用户确认分支 |
| `app/copilot/intents.py` | 增加价格确认状态、原因和覆盖信息 |
| `app/context/manager.py` | 为 Strategy 仅提供必要证据和已验证候选，减少上下文 |
| `app/agents/review.py` | 审核候选引用和跨 Artifact 语义，不再让模型重算数字 |
| `app/safety/content_revision.py` | 数字问题切换为模板重渲染，减少正则修补 |
| `app/copilot/facade.py` | 映射 `waiting_user` 价格确认响应和恢复入口 |
| `app/copilot_ui.py` | 分开展示核心参考、相邻档次和全市场范围，并提供价格确认操作 |
| `app/public_progress.py` | 展示前置价格检查和候选评估阶段 |

## 15. 增量实施路线

### 阶段 A：市场数据清洗与价格门控

目标：先阻止明显偏离市场的售价继续消耗 Strategy 和 Listing。

1. 增加可比商品归一化和重复样本处理。
2. 重新生成两个品类各 100 条带验收标签的样本，覆盖脏数据和极端但合理商品。
3. 实现纯函数 `clean_market_price_samples()`，使用 log-MAD、IQR 和数据质量规则复核异常价格。
4. 实现 `classify_market_layers()`，输出核心可比、相邻档次和全市场三层证据。
5. 输出清洗前后样本量、被排除样本、三层价格区间、核心均值、中位价和分布状态。
6. 增加阈值配置和 `MarketPriceAssessment`。
7. 实现纯函数 `assess_market_price_position()`。
8. 在 Market 后增加 `market_evidence_cleaner`、`comparable_market_classifier` 和 `market_price_gate`。
9. 增加等待用户、改价和保留原价的恢复协议。
10. 前端展示偏离百分比、核心建议区间、相邻档次、异常样本数量、样本质量和确认按钮。

验收重点：脏数据不会拖偏参考中心；极端但合理的商品不会被误删；不同档次不会混入核心均价；清洗和分层记录可复盘；在高或中可信证据下，超出 +/-15% 时不运行 Listing、Strategy、Review 和 Browser；用户确认后从新 Checkpoint 继续，不重复市场查询。

### 阶段 B：促销类型协议

目标：彻底消除“10 元”和“10%”混淆。

1. 新增 `PromotionSpec` 可辨识联合类型。
2. 废弃模型协议中的裸 `selected_discount`。
3. 工具统一归一化为 `discount_amount_yuan`。
4. 保留旧字段的只读兼容转换，Run Bundle 标记协议版本。

验收重点：错误单位组合无法通过 Pydantic；兼容层不会静默猜测单位。

### 阶段 C：动态候选与确定性评估

目标：让模型制定真正因商品而异的策略，同时把数学交给工具。

1. 模型动态提出 2 至 4 个候选。
2. 工具批量评估毛利、库存、市场位置和政策限制。
3. 单个候选失败只淘汰该候选。
4. 模型只从合格候选 ID 中选择。
5. 所有候选和淘汰原因写入审计 Artifact。

验收重点：不同品类得到不同促销形式；没有固定五档优惠；模型不能选中不合格候选。

### 阶段 D：数字所有权和渲染

目标：消除模型叙述与工具数字不一致。

1. 建立字段所有权表和运行时断言。
2. 最终数字文案改为模板渲染。
3. Review 的数字修复改为重渲染，不再全量调用 Strategy。
4. UI 同时展示基础售价、优惠类型、到手价、毛利率及市场偏离。

验收重点：故意让模型在理由中写错数字，最终用户页面仍只出现工具计算值。

### 阶段 E：稳定性、迁移与面试证据

目标：在真实 DeepSeek 模式下证明新协议稳定可解释。

1. 增加旧 Checkpoint 和旧 Strategy Artifact 的迁移器。
2. 增加阶段级超时、重选预算和降级终态。
3. Run Bundle 增加价格门控、候选生成、候选淘汰、最终选择和数字来源。
4. 运维端只读展示所有状态，不提供主动执行按钮。
5. 使用真实 DeepSeek 完成正常、偏高、偏低、低样本、候选全失败和恢复测试。

验收重点：错误准确收敛为 `waiting_user`、`business_rejected`、`degraded_completed` 或 `manual_attention`，不再以模糊 `workflow_failed` 代替业务状态。

## 16. v51-v59 版本化迭代计划

### 16.1 版本数量与执行原则

从当前 v50 到本路线目标状态，计划再完成 **9 个增量版本**：`v51` 至 `v59`。`v59` 是本轮 Strategy 定价重构的最终验收版，不代表项目此后不能继续增加真实平台接入等功能。

拆成 9 版的原因是把高风险变化分离：市场数据、统计清洗、可比性分类、流程门控、用户交互、促销协议、模型推理、确定性渲染和真实 API 验收不能在同一版同时修改。否则一旦失败，很难判断问题来自数据、算法、状态恢复还是模型输出。

后续每次迭代必须遵守：

1. 从上一版已通过验收的目录复制出新版本，不能跨过失败版本继续堆功能。
2. 先运行上一版完整回归，记录基线，再开始修改。
3. 每版只完成本节规定的职责；未列入该版的重构留到后续版本。
4. 新增协议必须先有 Pydantic Schema 和纯函数测试，再接入 Graph、UI 或真实模型。
5. 每版都更新本节的状态表、该版本技术说明和实际测试结果。
6. 合成测试标签只供测试读取，生产运行路径不得把标签交给 Agent 或统计函数。
7. 任何技术失败都必须收敛成具体阶段、错误码和恢复建议，不能只返回 `workflow_failed`。

版本状态表：

| 版本 | 核心交付 | 当前状态 | 进入下一版的硬门槛 |
| --- | --- | --- | --- |
| `v51` | 可控市场样本与数据版本 | passed | 两类各 100 条，离线标签和分布测试通过 |
| `v52` | 确定性异常清洗引擎 | passed | 脏数据被排除，合理极端不被直接删除 |
| `v53` | 三层可比市场证据 | passed | 核心、相邻、全市场稳定分层，目标价不参与选样 |
| `v54` | 市场价格门控与恢复 | passed | 偏离价格暂停，确认后从新 Checkpoint 恢复 |
| `v55` | 用户价格确认 UI 与只读运维证据 | passed | 三层口径可理解、可操作、可追踪 |
| `v56` | 促销类型和单位协议 | passed | 10 元、10%、九折不再混淆，旧数据可迁移 |
| `v57` | Strategy 动态候选与工具裁决 | passed | 模型动态提案，工具算数，单候选失败不终止任务 |
| `v58` | 确定性渲染、Review 和修正审计 | planned | 页面数字只有一个可信来源，修复不重跑整条链路 |
| `v59` | 真实 DeepSeek、端到端稳定性和面试证据 | offline passed / live external blocked | 533 项回归、UI、联动和 Run Bundle 通过；真实 API 待用户 Key |

### 16.2 v51：可控市场样本与数据版本

**目标**：先建立能够验证后续算法的数据，而不是先写异常识别代码再用普通样本自证正确。

**代码和数据修改**：

- 修改 `scripts/generate_market_samples.py`，让无线耳机和机械键盘各保持 100 条商品：94 条常规、2 条明显脏数据、4 条极端但合理商品。
- 为特殊样本增加 `test_case`、`expected_statistical_flag`、`expected_excluded`、`expected_market_layer` 和解释字段。
- 高端合理样本必须具有品牌层、功能、服务或组合装依据；低价合理样本必须具有清仓、翻新或渠道依据。
- 线上数据加载器剥离 `expected_*` 字段，只让测试夹具读取这些答案。
- 将 SQL 数据集版本从 `tenant-market-v3` 升级，并支持旧数据库自动安全重建。
- 生成数据分布报告，记录数量、最小值、最大值、均值、中位价和特殊样本 ID。

**必须新增或更新的测试**：

- `tests/test_v51_market_fixture_quality.py`：检查每类恰好 100 条、ID 唯一、价格和单位合法、94/2/4 构成正确。
- 检查脏数据与合理极端样本都真实存在，而不是只写标签不改变价格分布。
- 检查生产加载结果不含 `expected_*` 字段，防止答案泄漏。
- 检查 SQLite 两个品类数量、版本号和 JSON 源文件一致。
- 运行原有 Market、SQL、租户隔离和完整回归测试。

**完成条件**：本版只证明数据可用，不实现异常删除，不改变现有业务流程和 UI。

**实际完成记录（2026-08-29）**：

- 两类数据均达到 100 个商品和 200 条评论，构成为 94/2/4。
- 无线耳机原始价格范围为 9.9 至 9999 元，中位价 189 元；机械键盘为 8.8 至 12999 元，中位价 329 元。
- 特殊样本全部能被 log-MAD 或 IQR 至少一种方法识别为统计边界样本。
- 线上加载已剥离离线期望标签，SQL 数据集已升级到 `tenant-market-v4`。
- 竞品上下文采用最小字段投影，扩展样本元数据不会挤掉商家记忆。

### 16.3 v52：确定性异常清洗引擎

**目标**：用不调用大模型的纯统计程序判断价格可信度，并产生完整审计记录。

**代码修改**：

- 新增 `app/tools/market_statistics.py`。
- 实现币种、单品/套装、成色、非正价格、重复 SKU 和单位归一化。
- 实现对数价格、MAD modified z-score、IQR 复核和保守排除规则。
- 输出 `dirty_outlier`、`explainable_extreme`、`suspicious`、`retained` 等裁决状态。
- 原始记录只做逻辑排除，不从 JSON、SQLite 或 Run Bundle 物理删除。
- 增加保留比例、最少样本、均值与中位价差异、多峰分布降级规则。

**必须新增的测试**：

- `tests/test_v52_market_statistics.py`：极高和极低脏数据被识别，普通样本不被误删。
- 699 元高端耳机即使被统计标记，也因业务字段可解释而不进入 `excluded`。
- `MAD=0`、`IQR=0`、少于 5 条、重复样本、0 元、负数、单位无法换算全部有明确结果。
- 清洗后少于原样本 70% 或不足 5 条时返回 `advisory_only`。
- 删除离线期望标签后重新运行，输出仍与期望一致。

**完成条件**：统计模块保持纯函数、确定性、可重复；同一输入重复运行结果和哈希完全相同。

**实际完成记录（2026-08-29）**：

- 新增 `market-cleaning-v1` 协议与 `clean_market_price_samples()` 纯函数。
- 完成币种、元/分、单品/套装、成色、非正价格、重复 SKU 和不可识别单位处理。
- 使用 log-MAD 与 IQR 产生统计标记，并以业务解释保护清仓、翻新、高端和客制化商品。
- 两个品类均从 100 条原始商品中逻辑排除 2 条脏数据，保留 98 条；4 条合理极端均未误删。
- Market Artifact 保存原始/清洗后分布、100 条逐条裁决、警告和稳定 SHA-256 哈希。
- 新增 17 个 v52 定向测试，完整回归 482 项全部通过。

### 16.4 v53：核心、相邻档次和全市场三层证据

**目标**：解决“真实存在的高价商品是否应该进入当前商品均价”的可比性问题。

**代码修改**：

- 增加 `ComparableMarketInput`、`MarketLayerEvidence` 和三层统计 Artifact。
- 实现 `classify_market_layers()`，按照规范类目、产品形态、确认功能、目标人群、渠道、成色和品牌层建立匹配分数。
- 输出 `core_comparable`、`adjacent_tier`、`full_valid_market`；只有核心组产生 `core_reference_price`。
- 价格不得成为第一轮核心选样条件。价格门控通过后，才可形成 `price_neighborhood` 营销参考。
- 评论仅跟随各自商品进入对应证据层，避免高端商品评论污染普通档次痛点结论。
- 扩展 `ResearchEvidence`、Trace 和 Run Bundle，记录样本进入每一层的理由。

**必须新增的测试**：

- `tests/test_v53_market_layers.py`：普通游戏耳机、高端降噪耳机、清仓商品分别进入正确层。
- 把用户目标售价从 199 改为 699，核心样本集合必须保持不变，证明不存在按答案选样。
- 699 元高端耳机保留在全市场和相邻高端层，但不抬高普通耳机核心均价。
- 合理极端商品如果规格与目标商品确实同层，应进入核心组；分布不稳时参考中心回退中位价。
- 核心样本少于 5 条或多峰无法解释时只给建议，不形成硬门控。

**完成条件**：三层样本数量、价格区间、平均价、中位价和归类理由均可从 Run Bundle 独立复算。

**实际完成记录（2026-08-29）**：

- 新增 `market-layering-v1` 协议、`ComparableMarketInput`、`MarketLayerEvidence` 和 `classify_market_layers()` 纯函数。
- `ComparableMarketInput` 禁止额外字段且没有目标售价；199 元和 699 元两次任务得到相同核心样本及内容哈希。
- 无线耳机和机械键盘均从 98 条有效记录中形成 94 条核心与 4 条相邻记录；清仓、高端、翻新和组合装不再污染普通层参考价。
- 核心层评论只引用核心商品，相邻层评论只引用相邻商品；Market Artifact 保存三层分布、逐条归类原因和 SHA-256 哈希。
- 核心层稳定时使用均值，偏斜时回退中位价，少于 5 条或多峰时降级 `advisory_only`。
- 新增 9 个 v53 定向测试和独立验收脚本，独立验收通过，完整回归 `491 passed`；机器可读结果记录在 `reports/v53/v53_acceptance.json`。

### 16.5 v54：市场价格门控、暂停和恢复

**目标**：在 Listing 和 Strategy 消耗模型之前，先处理明显偏离市场的心理售价。

**代码修改**：

- 增加 `MarketPriceAssessmentInput`、`MarketPriceAssessment` 和集中阈值配置。
- 实现 `assess_market_price_position()`，只读取 `core_reference_price`。
- 在 Graph 中把 `market_price_gate` 放在 Market 分层之后、Listing 和 Strategy 之前。
- 超出阈值返回 `waiting_user / price_confirmation_required`，不能返回技术失败。
- 支持“采用建议价格”“保留原价并提供差异化依据”“只查看市场分析”。
- 用户回复后创建新 Checkpoint 继续，复用已有市场证据，不重复 SQL 查询和清洗。
- 对重复确认请求增加幂等键，保证只恢复一次。

**必须新增的测试**：

- `tests/test_v54_market_price_gate.py`：正常、偏高、偏低、成本与市场区间冲突、低证据质量四类状态。
- 偏离请求的事件中不得出现 Listing、Strategy、Review、Browser 调用。
- 用户改价后从下一节点恢复；市场工具调用次数不增加。
- 用户保留高价但没有差异化证据时继续等待；有确认依据时留下 `pricing_override`。
- 重复点击确认不会生成两个 Job 或执行两次店铺写入。

**完成条件**：价格门控是一种可恢复业务状态，不是失败；暂停前后状态版本、父子 Run 和审计事件完整。

**实际完成记录（2026-08-29）**：

- 新增 `market-price-gate-v1`、`MarketPriceAssessmentInput`、`MarketPriceAssessment` 和纯函数 `assess_market_price_position()`。
- 默认标准阈值为核心参考价上下 15%，并预留标准品 10% 和差异化商品 25% 配置；建议区间同时满足市场接受带和确定性毛利底价。
- DAG 增加 `market_price_gate_agent`；偏离时 Market 和价格门完成，Listing、Strategy、Review、Browser 均不启动。
- 会话层支持采用建议价、带可核验依据保留原价、只看市场分析三种动作，并续跑同一任务的新 Run。
- 恢复时复用 Market Artifact 和工具记录；Checkpoint 版本与 `client_request_id` 防止重复恢复。
- 新增 6 个 v54 定向测试和独立验收脚本；12 项机器验收全部通过，完整回归 `497 passed`，结果记录在 `reports/v54/v54_acceptance.json`。

### 16.6 v55：用户价格确认 UI 与只读运维证据

**目标**：让普通用户理解系统为什么暂停，让开发者能够查看证据但不能从运维端主动执行业务。

**代码修改**：

- 用户页面分开展示核心可比参考价、核心区间、相邻档次和全市场范围。
- 明确说明目标售价偏离比例、样本数量、被排除脏数据数量、证据可信度和建议区间。
- 提供三种价格确认操作，并把回复追加到当前对话任务而不是创建无关任务。
- 执行进度先显示“数据清洗”和“价格合理性检查”，暂停时后续阶段显示“尚未执行”。
- 运维后台只读展示三层样本、异常分数、排除原因、Checkpoint 和恢复事件，不保留主动审批执行按钮。
- 增加移动端和桌面端稳定布局，不能暴露测试标签或内部思维链。

**必须新增的测试**：

- `tests/test_v55_pricing_ui.py`：业务暂停、技术失败、正常放行使用不同文案和颜色。
- Playwright 桌面与移动截图：三层价格信息不重叠，操作按钮可见，历史会话不跳动。
- 用户选择三种操作后，API 请求、TaskSession 和 Checkpoint 对应正确。
- 运维页面没有会改变任务状态的按钮，刷新后能观察用户端最新状态。

**完成条件**：不了解 Agent 的用户只看对话和价格解释就能作出下一步选择，开发者证据仍保留在只读后台。

### 16.7 v56：促销类型、单位和旧协议迁移

**目标**：先修好 Strategy 的输入输出语言，再改模型推理，彻底消除优惠单位歧义。

**代码修改**：

- 在 `app/model/contracts.py` 定义以 `promotion_type` 为判别字段的 `PromotionSpec` 联合类型。
- 分别定义无优惠、固定金额券、百分比折扣、赠品或组合方案；不同类型只允许自己的字段。
- 废弃模型路径中的裸 `discount` 和 `selected_discount`，统一工具输出 `discount_amount_yuan`。
- 增加旧 Checkpoint、旧 Strategy Artifact 的只读迁移器；无法确定单位时拒绝静默猜测并要求重新生成。
- 协议版本写入 Artifact、Trace 和 Run Bundle。

**必须新增的测试**：

- `tests/test_v56_promotion_contracts.py`：10 元券、10%、九折得到不同且准确的结构。
- 非法字段组合无法通过 Pydantic，例如固定金额券携带百分比字段。
- 旧协议中单位明确的数据迁移成功，单位不明确的数据返回具体迁移状态。
- 序列化和反序列化后金额、比例、货币和协议版本不变化。

**完成条件**：本版可以继续使用旧 Strategy 行为，但所有进入新流程的促销数据必须先通过新 Schema。

### 16.8 v57：Strategy 动态候选与确定性工具裁决

**目标**：保留一个 Strategy Agent，但把它重构为证据选择、候选提案、工具评估和受限选择。

**代码修改**：

- `Evidence Selection` 最多自主选择两个有用的可选证据工具。
- `Candidate Proposal` 由真实模型根据品类、核心市场、相邻档次、评论、历史活动和目标动态提出 2 至 4 个方案，不能使用全品类固定优惠列表。
- `Candidate Evaluation` 由本地工具批量计算到手价、毛利率、库存、预算和资格。
- 单个候选不合格只淘汰该候选；模型只能选择 `eligible=true` 的候选 ID。
- 全部失败时允许一次受控候选修复；仍失败则向用户澄清，不进入死循环。
- 为证据选择、提案、JSON 修复和重选设置独立次数、Token 和超时预算。

**必须新增的测试**：

- `tests/test_v57_strategy_candidates.py`：耳机和键盘得到与品类相关、彼此不同的候选形式。
- Mock LLM 故意混淆 10 元和 10%，Schema 必须拒绝或受控修复。
- Mock LLM 故意手算错误，工具结果必须覆盖模型数字。
- 一个候选毛利失败时其他候选继续；模型试图选择失败 ID 时只允许一次重选。
- 模型连续三次失败、工具超时和全部候选失败均有受控终态和具体错误码。
- 证明不存在无限 Agent Loop，模型调用次数和工具调用次数不超过配置预算。

**完成条件**：策略创意来自模型，数学和可执行性来自工具；同一候选失败不再导致整个任务失败。

### 16.9 v58：确定性渲染、Review 和修正审计

**目标**：消除 Strategy、Listing、Review 和页面之间的数字与语义漂移。

**代码修改**：

- 建立数字所有权表和运行时断言：售价、优惠、到手价、毛利率、库存只来自确定性 Artifact。
- 新增 `Deterministic Strategy Renderer`，模型只提供非数字经营理由和表达建议。
- Listing、Strategy、Review 增加已确认功能、价格、优惠和目标人群的语义一致性检查。
- 数字错误通过重新渲染修复；宣传错误只重写对应字段；禁止为局部问题重跑整个 Strategy。
- 每次自动修正记录修正前值、修正后值、原因、证据引用和内容哈希。
- Review 只检查事实、宣传合规、候选引用和跨 Artifact 一致性，不再重新计算。

**必须新增的测试**：

- `tests/test_v58_strategy_rendering_review.py`：模型理由故意写错到手价和毛利率，最终页面仍只显示工具值。
- Listing 写入未确认功能时只删除或重写该字段，Strategy 结果和任务不整体失败。
- Review 对数字问题不触发 Strategy 模型调用；模型调用计数保持预期。
- 最终店铺写入内容与已审核 Artifact 哈希一致，浏览器回读验证通过。
- 修正审计可在 Run Bundle 和运维后台查看。

**完成条件**：任一最终业务数字都能追溯到唯一工具字段，任一自动修正都能说明改了什么和为什么。

### 16.10 v59：最终整合、真实 API 和面试证据

**目标**：证明整个路线在真实 DeepSeek、真实服务联动和异常场景下可稳定运行。

**代码和文档修改**：

- 完成旧会话、旧 Checkpoint 和旧 Artifact 的兼容测试；不兼容数据给出可理解的恢复建议。
- 完善阶段级超时、指数退避、熔断、降级、幂等和恢复终态。
- Run Bundle 汇总数据清洗、三层归类、价格门控、候选生成、工具裁决、模型选择、修正审计和浏览器证据。
- 运维、Trace、模拟商家后台和用户页面保持同一 `task_id/run_id/checkpoint_version` 联动。
- 更新用户测试指引、架构说明、故障演示、面试讲解和已知边界。
- 真实模型测试只使用配置中的 DeepSeek provider，不允许静默降级成 deterministic 后仍声称是真实模型。

**必须完成的测试**：

- 全量离线回归全部通过，既有测试数量不得因删除测试而下降。
- 使用真实 DeepSeek 完成：正常价格、明显偏高、用户改价恢复、高价有依据、动态候选、单候选失败、全部候选失败七类运行。
- 每次真实运行核对 `model_call_count`、实际 provider/model、工具调用、Token、耗时和降级原因。
- Playwright 完成用户生成、价格确认、店铺同步、后台联动和刷新恢复的端到端测试，并保留截图。
- 并发提交和重复确认只产生一次写入；工具失败后能够重试或恢复。
- 导出至少一个成功、一个等待用户、一个受控失败的完整 Run Bundle。

**最终完成条件**：满足第 18 节全部完成标准，并形成可现场演示、可离线复盘、可回答面试深挖问题的证据包。真实 API 受外部网络或额度影响时，可以把运行标记为外部阻塞，但不能用 Mock 结果替代最终真实验收。

### 16.11 每版交付记录模板

以后每个版本都应在本路线或版本技术文档中追加以下记录，避免只写“已完成”：

```text
版本：vXX
状态：planned / in_progress / passed / blocked
基于版本：vXX-1
修改文件：
新增协议及版本号：
新增测试：
完整回归结果：
真实模型是否参与：是/否；provider/model：
已知限制：
Run Bundle 或报告路径：
进入下一版结论：允许/不允许
```

### v55 实际交付记录

```text
版本：v55
状态：passed
基于版本：v54
核心修改：PriceConfirmationPrompt 1.0、CopilotResponse 1.7、专用价格确认 API、用户三层价格界面、只读运维清洗与分层审计
新增测试：tests/test_v55_pricing_ui.py，共 5 项
完整回归结果：502 passed in 49.40s
真实模型是否参与：否；本版离线验收使用 deterministic，未冒充真实 DeepSeek
视觉验收：桌面 1440x960、移动 390x844 均通过，无横向溢出和页面脚本错误
已知限制：促销类型协议属于 v56；Strategy 动态候选属于 v57
报告路径：reports/v55/v55_acceptance.json、reports/v55/visual_check.json
进入下一版结论：允许进入 v56
```

### v56 实际交付记录

```text
版本：v56
状态：passed
基于版本：v55
核心修改：PromotionSpec 1.0、规范人民币优惠字段、旧 Checkpoint/Strategy Artifact 只读迁移、Artifact/Trace/Run Bundle 协议证据
新增测试：tests/test_v56_promotion_contracts.py，共 12 项
完整回归结果：514 passed in 47.50s
真实模型是否参与：否；协议和回归使用 deterministic/Mock，避免把结构测试误报为真实 DeepSeek
已知限制：动态候选提案、工具批量裁决和失败候选淘汰属于 v57
报告路径：reports/v56/v56_acceptance.json
进入下一版结论：完整回归通过后允许进入 v57
```

### v57 实际交付记录

```text
版本：v57
状态：passed
基于版本：v56
核心修改：StrategyCandidate 1.0、模型动态提出 2 至 4 个候选、最多两个自主证据工具、evaluate_strategy_candidates 确定性裁决、单候选淘汰、一次候选修复、一次受控重选和强制收尾
新增测试：tests/test_v57_strategy_candidates.py，共 8 项
完整回归结果：522 passed in 48.19s
真实模型是否参与：否；本版自动验收使用 Mock/确定性工具，真实 DeepSeek 需按 README 单独验证
已知限制：确定性策略文案渲染、数字所有权运行时断言和跨 Artifact 修正审计属于 v58
报告路径：reports/v57/v57_acceptance.json
进入下一版结论：完整回归与验收通过后允许进入 v58
```

### v58 实际交付记录

```text
版本：v58
状态：passed
基于版本：v57
核心修改：Strategy Render v1、Numeric Ownership v1、Listing 字段级修正、Review 一致性检查、ExecutionPlan Artifact 哈希与载荷哈希绑定
新增测试：tests/test_v58_strategy_rendering_review.py，共 4 项
完整回归结果：526 passed in 47.90s
真实模型是否参与：否；本版自动验收使用 Mock/确定性工具，真实 DeepSeek 留给 v59 最终联动验收
已知限制：真实 DeepSeek 与 Playwright 的最终证据整合属于 v59
报告路径：reports/v58/v58_acceptance.json
进入下一版结论：验收和全量回归通过，允许进入 v59
```

### v59 实际交付记录

```text
版本：v59
状态：interview_ready；real_external_chain_validated=false（missing_api_key）
基于版本：v58
核心修改：旧 Checkpoint 兼容诊断、Run Bundle 2.5、真实 DeepSeek 证据门禁、ExecutionPlan 身份来源、四页面联动检查、最终发布状态 API
新增测试：tests/test_v59_final_integration.py，共 7 项
完整回归结果：533 passed in 53.34s
离线终态：成功、等待用户、受控技术失败各一个完整 ZIP Run Bundle
Playwright：14 项页面、布局、Trace、店铺和刷新恢复检查全部通过
真实模型是否参与：否；当前执行环境未配置 DEEPSEEK_API_KEY，报告明确标记 external_blocked，未用 Mock 替代
报告路径：reports/v59/offline_acceptance.json、compatibility.json、run_bundle_acceptance.json、browser_acceptance.json、live_deepseek_suite.json
最终结论：本机面试演示与离线复盘完成；配置用户 DeepSeek Key 并通过七场景脚本后，方可把真实外部链标记为已验证
```

## 17. 全路线必测场景

1. **正常价格**：中位价 219 元，用户售价 229 元，直接进入候选策略。
2. **明显偏高**：中位价 219 元，用户售价 300 元，暂停并建议约 189 至 249 元。
3. **明显偏低但仍有利润**：提醒低价定位风险并等待确认。
4. **低于毛利底线**：前置安全检查直接澄清，不执行 Market。
5. **高价有差异化依据**：用户确认品牌或独占服务后，以 `differentiated` 画像重新评估并留下审计。
6. **市场样本不足**：3 个样本只能作为低可信提醒，不冒充市场定论。
7. **单个极高价格**：`199/209/219/229/239/999` 中的 999 被双算法识别并留下排除审计，清洗后均值不会被拖高。
8. **单个极低价格**：0 元和单位无法换算的数据按质量规则排除；疑似秒杀价只标记，不冒充常规售价。
9. **真实高端价格层**：高端品牌样本先进入独立可比组，不被当作异常值删除。
10. **高端商品不污染普通层**：699 元高端耳机保留在全市场和高端相邻层，但不进入普通游戏耳机核心均价。
11. **同层合理极端价格**：有完整规格依据且与目标商品同层的价格保留在核心组；若导致均值和中位价差异超过 5%，门控回退到中位价。
12. **测试标签不泄漏**：移除 `expected_statistical_flag`、`expected_excluded` 和 `expected_market_layer` 后，算法得到的结果仍与离线期望一致。
13. **多峰且无法解释**：返回 `distribution_ambiguous` 和 `advisory_only`，不形成硬门控。
14. **过度清洗保护**：排除后少于原样本 70% 或不足 5 个时自动降级。
15. **均值与中位价分歧**：相差超过 5% 时使用中位价作为参考中心并提示分布偏斜。
16. **10 元券**：最终必须显示金额券和正确到手价，不能出现 10% 或九折。
17. **九折**：工具按 10% 计算，不能解释成 10 元。
18. **赠品成本未知**：只淘汰赠品候选，其他候选继续。
19. **一个候选毛利失败**：淘汰该候选，任务不失败。
20. **所有候选失败**：受控修复一次，之后向用户说明缺少可执行方案。
21. **Review 数字不一致**：重新渲染，不发生整段 Strategy 模型重试。
22. **恢复确认**：用户修改售价后沿原 TaskSession 新建 Checkpoint 继续。
23. **并发与幂等**：重复确认请求只能生成一次后续 Job。

## 18. 完成标准

达到以下条件后，才可认为本次 Strategy 重构完成：

- 市场明显偏离请求不会进入 Listing、Strategy 或写入阶段。
- 原始市场价格必须先经过可比性归一化和稳健异常值检测。
- 被排除的价格不会物理删除，其算法分数、来源和原因可审计。
- 核心可比、相邻档次和全市场三层证据具有独立统计口径，只有核心可比参考价能够触发价格门控。
- 极端但合理的商品保留在市场证据中，不因价格高低被自动删除，也不因真实存在就自动进入核心均价。
- 合成数据同时包含常规样本、可排除脏数据和极端但合理样本，并能用离线期望标签自动验收。
- 清洗后平均价与中位价差异过大时，系统自动回退到中位价。
- 默认 +/-15% 阈值集中配置，并可按品类画像调整。
- 高于市场价的信息同时出现在对话回答、市场页面和策略页面。
- 候选方案由模型按上下文动态提出，不使用全商品通用固定列表。
- 促销类型和单位由 Schema 明确区分。
- 到手价、毛利率和库存只来自确定性工具。
- 单候选错误不会终止整个任务。
- Review 不会因数字文案问题触发整段长文本重生成。
- 所有暂停、覆盖、候选淘汰和最终选择都能在 Trace 与 Run Bundle 中复盘。
- 真实 DeepSeek 测试中不再因优惠单位混淆或 Strategy 数字重算触发全局超时。

## 19. 最终设计原则

一句话概括本路线：

> 统计程序先判断价格是否可信，可比性分类再决定它属于核心、相邻档次还是全市场；只有核心可比参考价决定是否需要与用户确认。模型决定提出什么经营策略，工具决定策略在数学和库存上是否成立，程序决定最终数字如何展示，Review 只负责检查事实与表达是否一致。

这既保留了 Agent 的自主分析和动态策略能力，也把不可出错的价格、毛利、库存和权限边界交给可验证的程序协议。
