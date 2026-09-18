# 提交审计：§八 封顶（24 条判 1）逐条来源核验

- 日期：2026-09-18
- 状态：**尚未 apply**，等审计放行；`final_state` / 冻结 qrels / 评分均未运行
- 冻结规范：`ANNOTATION_INSTRUCTIONS.md` v4 第八节
- 核验对象：本轮 75 条裁决中，凡是「本块来源 ≠ 题目点名来源」且被判 1 的条目，共 24 条（q_0013 二十条、q_0019 四条）

判定口径（来自审计方）：题目点研究 A；本块来自研究 B；B 只是独立陈述相同事实、**没有**归因于 A → 封顶 1，这是规范要求，不因压低 HitRate / MRR 而改。

---

## 一、三条排除项与核验结果

| # | 需排除的情况 | 检查方式 | 结果 |
|---|---|---|---|
| 1 | 本块其实明确引用 / 归因于指定研究 | 在块内检索指定研究的作者姓氏（q_0013：Baruah / Nath / Deka / Kalita；q_0019：Annatelli / Mazzi / Pandeirada / Giannakoudakis / Rautiainen / Esposito / Thiyagarajan / Richel / Guigo / Sousa / Aricò）与指定研究标题的连续短语 | 24/24 无归因命中。唯一姓氏命中见第二节第 1 条 |
| 2 | 编号引用解析后其实是目标研究 | 取该块所属论文的**全篇文本**，检索指定研究的标题短语与 DOI。若该论文全篇根本不出现指定来源，块内任何编号引用都不可能解析到它 | 24/24 全篇未出现指定来源 → 编号引用路径排除 |
| 3 | 本块本身就是指定研究（source_group / 刊头识别出错） | 比对 `source_id`、DOI、论文标题、正文机构信息 | 24/24 均为不同论文，详见第三节明细的 DOI 列 |

补充（关于第 2 条）：这 24 条所属论文中有一部分确实带编号引用，但指定来源（`10.3389/fenrg.2018.00141` 与 `10.1039/d4gc00784k`）在这些论文的全篇文本中一次都没有出现，因此不存在「某编号指向目标研究」的可能。q_0019 的四条更有结构性保证：指定来源是 2024 年 Green Chemistry 综述，而四篇块所属论文分别为 2016—2018 年，时间上不可能引用它。

---

## 二、三处干扰命中，均判定无关

1. **n=93 命中姓氏 Triantafyllidis**。该词只出现在块内引用串 `(Tuck et al., 2012; Luque and Triantafyllidis, 2016)` 中，指向 2016 年 Luque & Triantafyllidis 的另一项工作，不是 2024 年《Beyond 2,5-furandicarboxylic acid》综述。同领域作者姓氏重合，非归因。

2. **n=75 / 76 / 78 / 83 / 93 与指定来源存在卷期号近似**。这五条块来自 *Frontiers in Chemistry* 2018, **Volume 6, Article 141**（Den et al.，DOI `10.3389/fchem.2018.00141`；正文自述 “Den et al. Lignocellulosics Transformation via Greener Pretreatment”）；而 q_0013 点名的是 *Frontiers in Energy Research* 2018, **Volume 6, Article 141**（Baruah et al.，DOI `10.3389/fenrg.2018.00141`）。**期刊与 DOI 不同，是两篇独立论文。**
   这条刻意列出：它正好说明仅凭刊头 / 卷期号 / 年份去认来源会误判。本轮采用的是 source_id + DOI + 正文机构信息，三者互不冲突。

3. **`according to` / `literature` 字样命中（n=71 / 84 / 86 / 88）全部是普通行文**：
   - n=84：`vary according to species, tissues and maturity`
   - n=86：`varies according to the type of biomass`
   - n=88：`According to their chemical composition, extractives can be divided into…`
   - n=71 / 88：`referred in the literature as holocellulose`
   均不含归因语义。

---

## 三、24 条明细

| n | query | 本块来源论文（≠ 点名来源） | 本块 DOI | 点名来源 DOI | 全篇是否提及点名来源 | 作者姓氏命中 |
|---|---|---|---|---|---|---|
| 69 | q_0013 | Sustainable production of furan-based oxygenated fuel additives from pentose-rich biomass residues | 10.1016/j.ecmx.2022.100222 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 70 | q_0013 | Total utilization of lignin and carbohydrates in Eucalyptus grandis | 10.1186/s13068-019-1644-z | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 71 | q_0013 | Lignocellulosic Biomass: A Sustainable Platform for Production of Bio-Based Chemicals and Polymers | 无 DOI 记录 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 72 | q_0013 | Lignocellulosic Biomass: A Sustainable Platform for Production of Bio-Based Chemicals and Polymers | 无 DOI 记录 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 73 | q_0013 | The conversion of lignocellulosics to levulinic acid | 无 DOI 记录 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 74 | q_0013 | Total utilization of lignin and carbohydrates in Eucalyptus grandis | 10.1186/s13068-019-1644-z | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 75 | q_0013 | Lignocellulosic Biomass Transformations via Greener Oxidative Pretreatment Processes（Den et al.） | 10.3389/fchem.2018.00141 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 76 | q_0013 | 同上（Den et al.） | 10.3389/fchem.2018.00141 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 77 | q_0013 | Review on the catalytic effects of AAEMs on pyrolysis / co-pyrolysis | 10.1016/j.jaap.2022.105479 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 78 | q_0013 | 同上（Den et al.） | 10.3389/fchem.2018.00141 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 79 | q_0013 | Lignocellulosic Biomass: A Sustainable Platform… | 无 DOI 记录 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 80 | q_0013 | The critical role of lignin in lignocellulosic biomass conversion… | 无 DOI 记录 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 81 | q_0013 | Total utilization of lignin and carbohydrates in Eucalyptus grandis | 10.1186/s13068-019-1644-z | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 82 | q_0013 | Literature Review on Furfural Production from Lignocellulosic Biomass | 10.4236/nr.2016.73012 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 83 | q_0013 | 同上（Den et al.） | 10.3389/fchem.2018.00141 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 84 | q_0013 | Lignocellulosic Biomass: A Sustainable Platform… | 无 DOI 记录 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 85 | q_0013 | The conversion of lignocellulosics to levulinic acid | 无 DOI 记录 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 86 | q_0013 | Literature Review on Furfural Production from Lignocellulosic Biomass | 10.4236/nr.2016.73012 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 88 | q_0013 | Production and Downstream Integration of 5-(Chloromethyl)furfural from Lignocellulose | 10.1021/acssuschemeng.3c05525 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 89 | q_0013 | Review on the catalytic effects of AAEMs… | 10.1016/j.jaap.2022.105479 | 10.3389/fenrg.2018.00141 | 否 | 无 |
| 90 | q_0019 | Lignocellulosic Biomass: A Sustainable Platform… | 无 DOI 记录 | 10.1039/d4gc00784k | 否 | 无 |
| 91 | q_0019 | Supported gold- and silver-based catalysts for selective aerobic oxidation of HMF to FDCA | 10.1039/c8gc01340c | 10.1039/d4gc00784k | 否 | 无 |
| 92 | q_0019 | Lignocellulosic Biomass: A Sustainable Platform… | 无 DOI 记录 | 10.1039/d4gc00784k | 否 | 无 |
| 93 | q_0019 | 同上（Den et al.） | 10.3389/fchem.2018.00141 | 10.1039/d4gc00784k | 否 | Triantafyllidis（见二.1，判定无关） |

> 注：源记录里 `paper_a39f1f9d…`、`paper_fc29dce7…`、`paper_d5bfa189…` 三条没有 DOI 字段，其来源身份由 `source_id` + 论文标题 + 正文自述共同确定，三者一致，非指定综述。

---

## 四、结论

- 24 条全部满足 §八 触发条件：题目点研究 A；本块来自研究 B；B 独立陈述相同事实且未归因于 A。
- 三条排除项全部不成立 → 按 v4 §八 规则 1，**这 24 条判 1 是规范要求**。（构成为：23 条 2→1，加 n=86 一条 3→1。上一轮我口头把它们统称「24 条 2→1」，此处更正为「23×2→1 + 1×3→1」。）
- 是否放行 apply，请审计给出结论。

---

## 五、另外 3 条「非同源来源」降级**不属于** §八（一并报备，避免混淆）

| n | query | 变化 | 依据 |
|---|---|---|---|
| 94 | q_0027 | 3→1 | 异论文（Molecules 2020, 25, 3574 的 FeCl₃/微波 RSM 优化），且陈述的是**不同事实**（200 ℃ / 0.5 min / FeCl₃），非同一事实 → L1c「不同论文的同类对照实验」 |
| 95 | q_0029 | 3→1 | 异论文（[Ch]Cl:Malic Acid + LiBr，157.3 ℃ / 1.74 min），条件与体系均不同 → L1c |
| 56 | q_0027 | 3→1 | **同一来源**论文（Eucalyptus 研究）。块内无 240 ℃ / 4 h / 30 atm H₂，属「块内没有所问数值」→ 1，与来源限定无关 |

---

## 六、待审计的三条边界题（本轮**未改**，也不视为已批准）

| n | query | 现状 | 争点 |
|---|---|---|---|
| 16 | q_0005 | 维持 2 | 块内 Table 1 给出 SnCl₄ 5→10 mol% 收率 52.4%→71.1%，但无「过量催化剂促进副反应」机理句。判 2（部分覆盖必需事实）还是 1（仅同体系对照） |
| 41 | q_0014 | 维持 3 | 四类（physical / chemical / physicochemical / biological）出现在 AUTHOR CONTRIBUTIONS 段（“drafted the portions on …”），结论段未给分类。作者贡献段是否构成实质陈述 |
| 96 | q_0044 | 维持 3 | 块内明确给出 ChCl/GA/H₂O (1:3:6) 与 160 ℃ / 10 min；「不外加催化剂」仅能由 Table 1 中 ZnCl₂ 对照行推得，块内无显式声明 |

---

## 七、当前未执行的动作（等放行）

- `closeout_semantic.py` 未加 `--apply`：judgments / master qrels 未写回
- `final_state.json` 未重建、`qrels_frozen_scope_1312.tsv` 未重生成
- 两个 primary run 未重评分
