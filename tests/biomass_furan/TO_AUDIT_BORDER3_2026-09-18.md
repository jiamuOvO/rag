# 提交审计：三条边界题材料（n=16 / n=41 / n=96）

- 日期：2026-09-18
- 状态：**未 apply**。三条的拟议裁决已写入 `verdicts_border3_proposed.json`，标记 `PROPOSED_PENDING_AUDIT`，等你裁定后才随 `--apply` 一起生效。
- 本文件只给材料与理由，不宣布"审计批准维持"。

---

## 一、n=16 / q_0005

**身份**
- `query_id` = `q_0005`　`doc_id` = `chk_cac3807d9a18c151af2918db`
- 组别 semantic_pending｜`old_grade` = 2｜**拟议** `final_grade` = **1**

**原题全文**
> 为什么2019年SnCl4/EMIMBr实验中的锡盐用量不能一味增加？
> （question_type = mechanism｜difficulty = medium｜split = dev｜answerable = True）

**必需事实（唯一一项，f1）**
> 过量催化剂在加速糠醛生成的同时也促进副反应，降低糠醛收率。

该项的标准依据句（`fact_evidence`，落在同一论文的另一块 `chk_1759d55f26111cb779e1d610`）：
> When the catalyst dosage increased from 5 mol% to 10 mol%, the furfural yield increased from 52.4% to 71.1%. As the catalyst dosage further increased, the furfural yield gradually decreased. **Excessive catalyst not only accelerated the formation of furfural from xylose but also promoted the side reactions, thus leading to the reduced furfural yield**

**硬性来源限定**
- 题目点名 `paper_db1a45d39737c05f08fff0fa`（*Efficient Synthesis of Furfural from Biomass Using SnCl4 as Catalyst in Ionic Liquid*, 10.3390/molecules24030594）
- 本块来源 = 同一 source_id → **同一来源成立**，§八 不触发

**完整块中与问题直接相关的原文**
> Table 1. Effect of different dosage of SnCl4 and MgCl2 on the dehydration of xylose into furfural. Entry SnCl4 (mol%) MgCl2 (mol%) Furfural Yield (%) 1 **10** 0 **71.1** 2 8 2 69.8 3 8 0 62.0 4 5 5 68.8 5 **5** 0 **52.4** 6 0 10 4.5 Reaction conditions: 200 mg xylose, 1000 mg of EMIMBr; 130 ◦C; 1 h.

块内另一处涉及副反应的句子（需注意其归属对象）：
> Besides, the formation of massive brown residues in **EMIMBF4** indicated the severe occurrence of side reactions.

**关于「必需事实里到底有没有机理这一项」**
有。f1 的文字本身由三个子句构成：①过量催化剂**加速**糠醛生成；②**同时促进副反应**；③因而降低收率。②是机理子句，不是可选项——它是这道 mechanism 题的题眼。

**为什么我倾向 2→1（而不是维持 2）**

1. 本块**不含机理子句**：没有任何一句把副反应归因于锡盐用量。块内唯一一句 `side reactions` 讲的是溶剂 EMIMBF4，与「锡盐用量不能一味增加」无关，不能借来用。
2. 本块的用量数据是 **5→8→10 mol% 单调上升**（52.4 → 62.0 → 71.1）。也就是说，只读这一块，得到的印象恰恰是"加大用量一直有效"，与题干的「不能一味增加」**方向相反**。块内没有超过 10 mol% 的数据，也没有任何"上升停滞/回落"的表述。
3. **同一题的既有口径已经先行**：本轮之前完成的 q_0005 四条（`chk_146ef0a2692f27a972ce10dc`、`chk_3802f88572be015e0e26a58d`、`chk_76e8cf8001bf01288fd488c1`、`chk_c637afc692e7621538a73442`、`chk_de8ed6767b301e54f3952681`）全部判 1。其中 `chk_76e8cf8001bf01288fd488c1` 甚至已经给出了**方向性下降**数据（催化剂:木糖 1:20 → 71.1%，1:40 → 54.2%），仍因"机理句缺失"判 **L1a = 1**。
4. 因此，本块比那一条**证据更弱**（那条至少支持了"不再提高"的方向），却拿 2，口径不一致。按 v4 §七「与其猜，不如判低；没有原文依据就不要给 2/3」，本块对该必需事实只构成"同一论文、同一体系的用量数据基准"= L1a/L1b **辅助关系**。

**可反方的说法（一并交出，供你权衡）**
若把 f1 机械拆成子句分别算覆盖，则子句①（用量增加→糠醛生成加快）在表中有直接数据支撑，于是可称"覆盖了一部分必需事实"而给 2。我认为这个拆法不成立：f1 是不可分的因果陈述，而对"为什么不能一味增加"这个提问而言，只给前半段等于没回答，且方向上误导。

---

## 二、n=41 / q_0014

**身份**
- `query_id` = `q_0014`　`doc_id` = `chk_45a69b0e755aa8b4a0a506fb`
- 组别 semantic_pending｜`old_grade` = 3｜**拟议** `final_grade` = **1**

**原题全文**
> 2018年预处理综述将现有木质纤维素预处理方法分为哪四类？
> （question_type = fact｜difficulty = easy｜split = test｜answerable = True）

**必需事实（唯一一项，f1）**
> 物理、化学、物理化学和生物方法。

该项的标准依据句（`fact_evidence`，位于同一综述的 **另一块** `chk_8c43d6189bfd58b10083cf8d`）：
> The pretreatment techniques currently in use, may be broadly classified as **physical, chemical, physicochemical, and biological** processes.

**硬性来源限定**
- 题目点名 `paper_2467240e9bec8a52db54345d`（*Recent Trends in the Pretreatment of Lignocellulosic Biomass for Value-Added Products*, 10.3389/fenrg.2018.00141）
- 本块来源 = 同一 source_id → **同一来源成立**，§八 不触发。问题不在来源，在"块里有没有这句分类陈述"。

**完整块中与四类名称有关的全部原文**（本块中四类名称**只出现这一处**）
> **AUTHOR CONTRIBUTIONS** JB and BN drafted the portions on **physical, chemical and physicochemical** pretreatments. RS and SK drafted the portion on **biological** pretreatments. RD and DB edited the portions on chemical and physicochemical pretreatments. EK planned the outlay of the review, supervised the development of topics and edited the final manuscript. **ACKNOWLEDGMENTS** EK, JB, SK, and RS wish to acknowledge DBT, Government of India, for the Twinning Research Grant (Grant No. BT/PR16008/NER/95/47/2015).

**句子语义功能分析（按你的重点）**

1. 主语是**人**（JB、BN、RS、SK、RD、DB、EK），谓语是 `drafted / edited / planned / supervised`，四类名词是 `drafted` 的**宾语**。这句话的命题内容是"谁负责撰写综述的哪一部分"，即**署名归属**。
2. 它不是对预处理方法作分类的陈述。真正的分类陈述（`may be broadly classified as …`）不在本块，在上文引的另一块。
3. 本块的实质正文是 **CONCLUSION** 段：谈各种预处理方法各有优劣、成本与环保瓶颈、反应机理尚未摸清等，通篇**没有**出现"分为四类"或任何分类框架。
4. 因此，"四类名称出现了"在这里属于**元数据（稿约）语境**，与 v4 §四 列举的"书目条目、致谢、刊头属无实质内容"同类。按你的口径——**出现名称本身不等于支持问题答案，要看句子的语义功能**——本块不构成对该必需事实的实质陈述。
5. 本块并非整块都是元数据（前面有实质的结论段），所以不判 0；它与问题的关系只是"同一篇论文里对预处理方法的总体评述"，属 **L1b＝1**。

**旧判 3 的成因**：把"作者贡献段里出现了四类名称"直接当成"综述给出了四类分类"，忽略了句子的语义功能。

**可反方的说法**：从这句话确实能**推出**该综述包含这四个部分，做题者据此也能答对四类。若你的口径是"只要能从块内文字可靠恢复答案即算覆盖"，则应维持 3。我按前一条（语义功能）判，倾向 1。

---

## 三、n=96 / q_0044

**身份**
- `query_id` = `q_0044`　`doc_id` = `chk_eec4496762bf30d4c915fc71`
- 组别 semantic_pending｜`old_grade` = 3｜**拟议** `final_grade` = **2**

**原题全文**
> 限定不外加催化剂且处理葡萄糖/木糖混合物：2021年DES/MIBK研究采用什么DES组成及微波反应条件？
> （question_type = constraint｜difficulty = medium｜split = test｜answerable = True）

**必需事实（两项）**
- **f1**：采用ChCl/GA含水DES，不外加催化剂。
- **f2**：糖混合物的最佳微波条件为160℃、10 min。

两项的标准依据句（`fact_evidence`，同在另一块 `chk_305f10f1a75a63236a4c181e` = 该论文摘要）：
> f1: Choline chloride (ChCl) / glycolic acid (GA) deep eutectic solvent (DES) media with high water content but **without any additional catalyst** are introduced
> f2: The optimal reaction conditions were **160°C and 10 minutes for the sugar mixture** and 170°C and 10 minutes for birch sawdust in a microwave reactor.

**硬性来源限定**
- 题目点名 `paper_107512d3ed43f43f9bd8f45e`（*Furfural and 5-Hydroxymethylfurfural Production from Sugar Mixture Using Deep Eutectic Solvent/MIBK System*, 10.1002/open.202100163）
- 本块来源 = 同一 source_id → **同一来源成立**，§八 不触发

**完整块中与问题直接相关的原文（表格逐行保留）**
> Table 1. HMF and furfural yields using various feedstocks and extraction conditions.
> Entry ｜ Conditions ｜ HMF yield [%] ｜ Furfural yield [%]
> 1 ｜ Glucose as feedstock ｜ 10 ｜ –
> 2 ｜ Xylose as feedstock ｜ – ｜ 62
> **3 ｜ Glucose/xylose as feedstock ｜ 9 ｜ 49**
> **4 ｜ ZnCl2 as catalyst, glucose/xylose as feedstock ｜ 10 ｜ 51**
> 5 ｜ Fructose/xylose as feedstock ｜ 17 ｜ 49
> 6 ｜ HMF as feedstock (+additional extraction) ｜ 45 (+11) ｜ –
> 7 ｜ Furfural as feedstock ｜ – ｜ 98
> 8 ｜ HMF as feedstock, EtOAc extraction ｜ 50 ｜ –
> 9 ｜ Furfural as feedstock, EtOAc extraction ｜ 91 ｜ –
> 10 ｜ HMF as feedstock, optimized extraction ｜ 67 ｜ –
>
> 表注：**160°C reaction temperature and ChCl/GA/H2O (1:3:6) DES were used in all reactions.** When HMF/furfural was used as a feedstock, reaction time was limited to 2 minutes, **otherwise, it was set at 10 minutes**. MIBK was used as extracting solvent unless otherwise stated.

**按你点名的四个问题逐条作答**

1. **哪一行才是目标条件？** → **Entry 3（Glucose/xylose as feedstock）**。题目限定"处理葡萄糖/木糖混合物"，只有 entry 3 与 entry 4 是这个原料，其余各行是单糖、果糖、HMF 或糠醛进料。

2. **ZnCl2 行是实验组还是对照组？** → 它是与 entry 3 **并列的实验变体行**（同原料、外加 ZnCl2），不是经典意义上的空白对照；表内没有任何一行被标注为 blank/control。它的价值在于**对照关系**：entry 4（10 / 51）与 entry 3（9 / 49）几乎相同 → 说明外加 ZnCl2 并不带来提升。

3. **"无外加催化剂"是表中明确条件，还是推断出来的？** → **是推断出来的，不是明确条件。** 表注只固定了三件事：温度 160 ℃、DES 组成为 ChCl/GA/H2O (1:3:6)、反应时间规则（非 HMF/糠醛进料为 10 min），外加萃取溶剂 MIBK。**全块没有任何一句写"without additional catalyst / 不外加催化剂"。** 该结论只能由两件事推出：entry 3 的条件文字里**没有**列催化剂（缺失），以及 entry 4 是全表唯一显式写 `as catalyst` 的行（对照关系）。按本项目一贯纪律，缺失与对照关系只能用于定位，不能直接充当"支持"的原文依据。

4. **产率/时间/温度条件是否与问题一致？** → **温度一致**（表注 160 ℃，与 f2 一致）；**时间一致**（混合物进料按表注为 10 min，与 f2 一致）；**DES 组成一致**（ChCl/GA/H2O 1:3:6，属 f1 的"ChCl/GA 含水 DES"）；**产率**（entry 3：HMF 9%、糠醛 49%）— 问题未要求产率，仅作旁证。

**结论（拟议）**
- f2 → 明确覆盖 ✓
- f1 → **一半明确覆盖**（含水 ChCl/GA DES，且比率 1:3:6 明文给出），**另一半（不外加催化剂）无原文依据，只能推断** → f1 只算部分覆盖
- 两项未同时具备明确原文依据 → 不能判 3；已覆盖 f2 与 f1 的一半 → **2**

**一致性依据**：同一题 n=31（`chk_b57774d3d4688682f7455127`，给出 ChCl/GA/H2O 1:3:10 + 160 ℃/10 min，同样缺"不外加催化剂"）本轮判 **2**。若 n=96 维持 3，则同题内两条口径互斥。旧判 3 正是这个不一致的来源。

---

## 四、影响面与当前状态

| 项 | 现值 | 若三条拟议全部通过 |
|---|---|---|
| 75 条中变更数 | 48（现状 2/3 级） | **51** |
| 75 条中维持数 | 27 | 24 |
| 三条的处置 | 16 维持 2｜41 维持 3｜96 维持 3 | 16→1｜41→1｜96→2 |
| 全 100 条变更数 | 55（48+7） | **58** |

**当前未执行**：`closeout_semantic.py` 只跑了 dry-run（输出见下），judgments、master qrels、`final_state.json`、`qrels_frozen_scope_1312.tsv`、两个 run 的评分**全部未动**。

dry-run 输出（含三条覆盖项）：
```
border3 overrides: [16, 41, 96] status=['PROPOSED_PENDING_AUDIT']
labels on disk: 2996 pairs (60 queries)
verdicts to apply: 75 (changed 51, kept 24)
transitions: [((2,0),1), ((2,1),26), ((2,3),1), ((3,1),5), ((3,2),18)]
dry-run: nothing written
```

三条裁定后，我一次性执行 `closeout_semantic.py --apply`，然后依次：重建最终状态账 → 重建 frozen-scope qrels → 校验 grade 分布与唯一主键 → 两个 primary run 用同一快照重评分 → 出最终交付。不重开其他条目、不做失败重试或新增抽查。
