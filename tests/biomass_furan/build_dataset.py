"""Build evidence-backed candidate questions. Never infer negative labels from non-matches."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter

from freeze import ROOT, jsonl, write_json


def norm(text):
    return re.sub(r'\s+', ' ', text).strip()


# These passages were read during authoring. Quotes are checked against frozen text.
# alias: (existing chunk ID, verbatim passage after whitespace normalization)
E = {
 'sn_dose': ('chk_1759d55f26111cb779e1d610', 'When the catalyst dosage increased from 5 mol% to 10 mol%, the furfural yield increased from 52.4% to 71.1%. As the catalyst dosage further increased, the furfural yield gradually decreased. Excessive catalyst not only accelerated the formation of furfural from xylose but also promoted the side reactions, thus leading to the reduced furfural yield'),
 'sn_screen': ('chk_c637afc692e7621538a73442', 'Reaction conditions: 200 mg of xylose was dissolved in 1000 mg of EMIMBr; molar ratio of catalyst:xylose = 1:10; 130 ◦C; 1 h.'),
 'sn_temp': ('chk_3802f88572be015e0e26a58d', 'The maximum furfural yields at 120, 130, and 140 ◦C were 66.5, 71.1, and 70.2%, obtained at 1.5, 1.0, and 0.5 h, respectively.'),
 'sn_xylan': ('chk_a31155b8bf9d90dce8836014', 'We attempted to convert xylan into xylose in the EMIMBr/SnCl4 system at 130 ◦C. As shown in Figure 9, the furfural yield was 41.7% after 30 min reaction. The maximum furfural yield of 50.1% was obtained at 1 h. When the reaction time was prolonged to 4 h, the furfural yield declined to 33.0%.'),
 'des_opt': ('chk_fbc7a95665f94f660bc1af2c', 'the optimal values determined for the three parameters are: 1.74 min of reaction time, 157.3 ◦C and 8.19 wt.% of LiBr. For this set of conditions, the furfural yield predicted is 92.2%. To address the veracity of the determined values, assays were conducted using the abovementioned conditions. The maximum furfural yield experimentally determined was 89.5 ± 0.3%'),
 'des_mech': ('chk_e59b9573578a2ef6e31f67e2', 'the metal cation is responsible for increasing the C–O–H and C–O–C bond lengths, through the interaction with the hydroxyl groups, and lowering the energy necessary to break such C–O bonds.'),
 'cmf_sep': ('chk_80cfb9a0ecf7628f4f48e9d3', 'CMF is extracted in situ using immiscible organic solvents, allowing for an easy product separation.'),
 'cmf_def': ('chk_eddc849e74ee8a05d3379f29', 'The Bergius−Rheinau, or Bergius− Willstätter−Zechmeister process, is the hydrolysis of biomass by the action of fuming hydrochloric acid at a low temperature in one single step.'),
 'components': ('chk_5df905ea50c0c8b3f5c8b61d', 'LCB is mainly composed of three polymers: cellulose (C6H10O5)n, hemicellulose (C5H8O4)m, and lignin [C9H10O3(OCH3)0.9−1.7]x along with minor amounts of other compounds such as proteins, ash, and pectin'),
 'cellulose': ('chk_5df905ea50c0c8b3f5c8b61d', 'Cellulose is a main structural and integral part of LCB which is a linear polysaccharide and consists of Dglucose subunits linked by β-(1,4)-glycosidic bonds'),
 'solubility': ('chk_5df905ea50c0c8b3f5c8b61d', 'This polymer is insoluble in water unless at extremely low or high pH levels. However, it is soluble in solvents like ionic liquids (ILs) and N-methylmorpholine-N-oxide (NMMO)'),
 'recalcitrance': ('chk_7764b60e09f53eaa5aa68fc6', 'The presence of lignin renders the bio-polymeric structure highly resistant to solubilization thereby inhibiting the hydrolysis of cellulose and hemicellulose'),
 'pretreat': ('chk_8c43d6189bfd58b10083cf8d', 'The pretreatment techniques currently in use, may be broadly classified as physical, chemical, physicochemical, and biological processes.'),
 'supercritical': ('chk_52753142bb95f2c173a84e2a', 'When temperature and pressure of a fluid rise higher than relating values defining the critical point of the solvent, this fluid belongs to the family of supercritical solvents.'),
 'sc_co2': ('chk_52753142bb95f2c173a84e2a', 'Hydrothermal conversion of D-xylose and hemicelluloses to furfural by simultaneously furfural extraction with supercritical CO2 (sc-CO2) in catalyst-free conditions has been studied'),
 'sc_conditions': ('chk_52753142bb95f2c173a84e2a', 'a maximum furfural yield of 68% was furnished from 4% D-xylose initial loading at 230◦C for 25 min at the pressure of 12 MPa and 3.6 g/min CO2 flow rate.'),
 'phosphoric': ('chk_7e3320c3ad73bbf359dec07d', 'This paper explored the disentanglement of lignocellulose into hemicellulose-derived sugars, cellulose, and lignin in a biphasic solvent system (water/2-methyltetrahydrofuran) using phosphoric acid as recyclable catalyst.'),
 'phos_cycles': ('chk_7e3320c3ad73bbf359dec07d', 'a downstream and recycling strategy enabled recovery of phosphoric acid without loss of process efficiency over four consecutive cycles.'),
 'phos_swelling': ('chk_979045fd6bdb126a66d66d75', 'the lignocellulose is mixed with phosphoric acid (52 wt%) and heated to 80°C for 1 h.'),
 'phos_dilute': ('chk_979045fd6bdb126a66d66d75', 'In the fractionation step, the acid is diluted with water to 8 wt%. 2-MTHF (equal volume to the aqueous phase) is added as a second phase'),
 'phos_recycle': ('chk_61fec0ffa9e553e9cc7bc451', 'After phase separation and water evaporation, the phosphoric acid can be reused.'),
 'euc_order': ('chk_6f9e1ddf4e91431e9cfc6c46', 'Lignin was depolymerized into well-defined monomeric phenols in the first step using a Pd/C catalyst.'),
 'euc_cond': ('chk_6f9e1ddf4e91431e9cfc6c46', 'The maximum phenolic monomers yield of 49.8 wt% was achieved at 240 °C for 4 h under 30 atm H2.'),
 'euc_sugars': ('chk_6f9e1ddf4e91431e9cfc6c46', 'High retention of cellulose and hemicellulose pulp was also obtained, which was treated with FeCl3 catalyst to attain 5-hydroxymethylfurfural, levulinic acid and furfural simultaneously.'),
 'euc_yields': ('chk_6f9e1ddf4e91431e9cfc6c46', 'The optimal reaction condition for the co-conversion of hemicellulose and cellulose was established as 190 °C and 100 min, from which furfural and levulinic acid were obtained in 55.9% and 73.6% yields, respectively.'),
 'ga': ('chk_305f10f1a75a63236a4c181e', 'Choline chloride (ChCl) / glycolic acid (GA) deep eutectic solvent (DES) media with high water content but without any additional catalyst are introduced'),
 'ga_cond': ('chk_305f10f1a75a63236a4c181e', 'The optimal reaction conditions were 160°C and 10 minutes for the sugar mixture and 170°C and 10 minutes for birch sawdust in a microwave reactor.'),
 'ga_water': ('chk_305f10f1a75a63236a4c181e', '10 equivalent quantities of water (32.9 wt.%) were revealed to be beneficial for conversions without rupturing the DES structure.'),
 'ga_role': ('chk_01750ad0d20e4542372b9450', 'Applying only DES in a conversion reaction relies on the carboxylic acid acting as an HBD, which gives the DES acidic characteristics, and therefore, the DES itself can act as both the solvent and the catalyst.'),
 'ga_compare': ('chk_34018350f5510abb45085aed', 'Glucose produced a minor 10% HMF yield while xylose produced a much higher furfural yield, 62%'),
 'ga_zn': ('chk_34018350f5510abb45085aed', 'adding ZnCl2 as a catalyst did not improve the HMF yield, and using fructose as feedstock increased the HMF yield only from 10% to 17%'),
 'pt_cond': ('chk_86d0eecaed56b884bdba46b2', 'The reaction was firstly conducted at 100 °C under 1 MPa H2 in water.'),
 'pt_bulk': ('chk_86d0eecaed56b884bdba46b2', 'When 5%Pt@g-C3N4 was used as the catalyst, furfuryl alcohol was found to be the main product with 60.2% yield based on furfural within 5 h'),
 'pt_sheet': ('chk_86d0eecaed56b884bdba46b2', 'when g-C3N4 nanosheets was used as the support for loading Pt nanoparticles, significant improvement of activity with complete conversion and high selectivity of furfuryl alcohol of >99% could be achieved in 5 h'),
 'pt_mech': ('chk_0add36bde3d8d23e7501bd97', 'The large specific surface area, uniform dispersion of Pt nanoparticles and the stronger furfural adsorption ability of nanosheets contributed to the considerable catalytic performance.'),
 'pef_grade': ('chk_fcafe2465c1c9252a4a15ad2', 'Mn > 30 kg mol−1, conversion > 95%, colour-free products'),
 'pef_problem': ('chk_fcafe2465c1c9252a4a15ad2', 'melting point of such mixture of cyclic oligomers lies around 370 °C, well above the degradation temperature of PEF (~329 °C).'),
 'pef_solution': ('chk_fcafe2465c1c9252a4a15ad2', 'This challenge can be overcome, exploiting the self-plasticising effect of the forming polymer itself (which melts around 220 °C) by initiation in the presence of a high boiling, yet removable, and inert liquid plasticiser.'),
 'pef_rop': ('chk_9757e01c23afde940d264b95', 'Purified cyOEF can then be polymerised via catalytic ring-opening polymerisation (ROP) within less than 30 min with appropriate plasticisation and initiator to high molecular weight bottle-grade PEF.'),
 'real': ('chk_4b8602523106c29e88b03e83', 'the conversion of biomass is much more challenging than model carbohydrates because the decomposition behavior of the feedstock depends on the interactions between the cellulose, hemicellulose, and lignin.'),
 'hmf_recovery': ('chk_2aa52a1d53151245ba3fcd5b', 'HMF also encompasses some issues, such as its hydrophilic and polar behaviour that prevents its recovery from aqueous media, and a fast degradation due to the formation of dimers, oligomers and humins.'),
 'fdca': ('chk_2aa52a1d53151245ba3fcd5b', 'it can be easily converted by oxidation into 2,5-furandicarboxylic acid (2,5-FDCA), the key monomer for the synthesis of poly(ethylene 2,5-furandicarboxylate) (2,5-PEF)'),
 'sac_sites': ('chk_c189cd855d6d974fa4f8ee42', 'The Pt atoms are responsible for the activation of H2 and the Nb sites activate C-OH in the reaction.'),
 'sac_difficulty': ('chk_c189cd855d6d974fa4f8ee42', 'hydrogenation of C=O bond in HMF is more favourable than C–OH both kinetically and thermodynamically'),
 'sac_result': ('chk_c189cd855d6d974fa4f8ee42', 'the SACs can efficiently catalyze the hydrogenation of HMF to MF using H2 as the reducing agent with MF selectivity of >99% at complete conversion, while the selectivities of the metal nanocatalysts supported on Nb2O5 are very poor.'),
 'flash_roles': ('chk_765cb061e1f7730e719a8e05', 'The PdO layer is active for the Grob fragmentation of formaldehyde (HCHO) from glucose, which is subsequently in-situ steam reformed into syngas (i.e. H2 and CO), whereas the Pd0 core is active in promoting the last dehydration step for the formation of furfural.'),
 'flash_formula': ('chk_765cb061e1f7730e719a8e05', 'Furfural (C5H4O2) is an important platform chemical'),
 'flash_yields': ('chk_765cb061e1f7730e719a8e05', 'for the production of furfural by flash pyrolysis of lignocelluloses at 400 °C. For both dry and wet C6 cellulose and its monomers, the furfural yields reach 74–82 mol%, relative to 96 mol% from C5 xylan and 23–33 wt% from sugarcane bagasse and corncob.'),
 'photo': ('chk_8812eb342f37103548b3697f', 'fulvic acid forms complexes with Al3+ ions that exhibit solar absorption and photocatalytic activity for glucose conversion to HMF in one-pot reaction, in good yield (~60%) and at moderate temperatures (80 °C).'),
 'photo_ligands': ('chk_8812eb342f37103548b3697f', 'When using representative components of fulvic acid, catechol and pyrogallol as ligands, 70 and 67% HMF yields are achieved, respectively, at 70 °C.'),
}

# Each row: split, type, difficulty, language, question, [(fact, passage alias), ...].
Q = [
 ('dev','condition','easy','zh','2019年SnCl4/EMIMBr研究比较金属盐催化剂时，木糖与离子液体各用了多少，催化剂与木糖的摩尔比是多少？',[('木糖200 mg、EMIMBr 1000 mg，催化剂:木糖摩尔比1:10。','sn_screen')]),
 ('dev','condition','medium','en','In the 2019 SnCl4/EMIMBr xylose study, how does the time needed for the maximum furfural yield change between 120, 130 and 140 °C?',[('The respective times are 1.5, 1.0 and 0.5 h.','sn_temp')]),
 ('dev','data','easy','zh','在2019年EMIMBr/SnCl4体系中，130℃下木聚糖反应半小时的糠醛收率和最高收率分别是多少？',[('30 min时为41.7%；1 h达到最高50.1%。','sn_xylan')]),
 ('dev','data','medium','zh','2019年SnCl4催化木糖实验中，将用量从5 mol%提高到10 mol%后，糠醛收率怎样变化？继续增加用量是否仍提高收率？',[('收率由52.4%提高到71.1%，继续增加用量反而下降。','sn_dose')]),
 ('dev','mechanism','medium','zh','为什么2019年SnCl4/EMIMBr实验中的锡盐用量不能一味增加？',[('过量催化剂在加速糠醛生成的同时也促进副反应，降低糠醛收率。','sn_dose')]),
 ('dev','paraphrase','medium','zh','在2019年的锡盐—咪唑鎓溴盐体系里，以木聚糖为原料，达到最佳呋喃甲醛收率后继续煮到四小时，产率会怎样？',[('1 h达到50.1%，延长至4 h下降到33.0%。','sn_xylan')]),
 ('dev','condition','medium','zh','2021年氯化胆碱/苹果酸加LiBr制糠醛研究的优化实验，温度、反应时间和LiBr质量分数各是多少？',[('157.3℃、1.74 min、LiBr 8.19 wt.%。','des_opt')]),
 ('dev','data','medium','en','For the optimized LiBr-containing DES experiment in the 2021 alkali-halide study, distinguish the predicted furfural yield from the experimentally measured yield.',[('The prediction is 92.2%; the measured yield is 89.5 ± 0.3%.','des_opt')]),
 ('dev','mechanism','medium','zh','2021年碱金属卤化物促进糠醛生成的论文怎样解释金属阳离子对C—O键断裂的作用？',[('阳离子与羟基作用，增加C—O—H和C—O—C键长，降低断裂C—O键所需能量。','des_mech')]),
 ('dev','fact','easy','zh','2023年将浓盐酸糖液直接转成CMF的研究采用什么办法实现CMF分离？',[('用不混溶有机溶剂原位萃取CMF。','cmf_sep')]),
 ('dev','definition','easy','en','How does the 2023 CMF integration paper define the original Bergius–Rheinau saccharification process?',[('A single low-temperature biomass hydrolysis step using fuming hydrochloric acid.','cmf_def')]),
 ('dev','no_answer','medium','zh','2023年CMF集成工艺论文是否给出了其商业装置2025年全年实际运行的CMF吨产品成本及逐月停机记录？',[]),

 ('test','fact','easy','zh','2018年《Recent Trends in the Pretreatment of Lignocellulosic Biomass》综述列出木质纤维素的哪三种主要聚合物组分？',[('纤维素、半纤维素和木质素。','components')]),
 ('test','fact','easy','zh','2018年预处理综述将现有木质纤维素预处理方法分为哪四类？',[('物理、化学、物理化学和生物方法。','pretreat')]),
 ('test','fact','easy','en','In the 2021 recyclable-phosphoric-acid fractionation study, what two solvents form the biphasic medium?',[('Water and 2-methyltetrahydrofuran.','phosphoric')]),
 ('test','fact','easy','en','Which catalyst and class of monomer products were used and obtained in the first lignin-processing step of the 2020 Eucalyptus grandis integrated biorefinery study?',[('Pd/C was used to depolymerize lignin into monomeric phenols.','euc_order')]),
 ('test','fact','easy','zh','2021年糖混合物/DES-MIBK研究中，氯化胆碱与哪一种酸组成所研究的深共熔介质？',[('乙醇酸，即glycolic acid（GA）。','ga')]),
 ('test','fact','easy','en','How many consecutive phosphoric-acid recovery cycles maintained process efficiency in the 2021 lignocellulose fractionation study?',[('Four consecutive cycles.','phos_cycles')]),
 ('test','fact','medium','zh','2024年《Beyond 2,5-furandicarboxylic acid》综述说明，HMF通过什么转化成为用于2,5-PEF合成的二酸单体？',[('HMF通过氧化转化为2,5-呋喃二甲酸（2,5-FDCA）。','fdca')]),
 ('test','definition','easy','zh','2018年木质纤维素预处理综述如何从单体和连接键定义纤维素的结构？',[('纤维素是由D-葡萄糖亚基通过β-(1,4)-糖苷键连接的线性多糖。','cellulose')]),
 ('test','definition','easy','en','In the 2018 hemicellulose-to-furfural review, what temperature and pressure relation defines a supercritical solvent?',[('Both temperature and pressure exceed the values at the solvent critical point.','supercritical')]),
 ('test','definition','medium','en','Which molecular-weight, conversion and color criteria define bottle-grade material in the 2018 cyclic-oligomer PEF study?',[('Mn >30 kg/mol, conversion >95%, and color-free products.','pef_grade')]),
 ('test','definition','easy','zh','2018年环状低聚物制PEF论文中的ROP指什么聚合过程，所用直接原料是什么？',[('ROP为开环聚合，直接原料是纯化的环状PEF低聚物cyOEF。','pef_rop')]),
 ('test','definition','medium','zh','2021年糖混合物/DES-MIBK论文为什么把羧酸型DES称为兼具溶剂和催化剂作用的介质？',[('羧酸作为氢键供体赋予DES酸性，因此DES本身兼具溶剂和催化剂作用。','ga_role')]),
 ('test','condition','medium','zh','2021年可回收磷酸分级论文的初始溶胀步骤采用何种酸浓度、温度和时间？',[('磷酸52 wt%，80℃，1 h。','phos_swelling')]),
 ('test','condition','medium','en','After swelling in the 2021 phosphoric-acid study, what acid concentration and organic-to-aqueous volume ratio are used for fractionation?',[('Dilute the acid to 8 wt% and add an equal volume of 2-MTHF to the aqueous phase.','phos_dilute')]),
 ('test','condition','medium','zh','2020年Eucalyptus grandis集成炼制研究达到最高酚类单体收率时，温度、时间和氢气压力是多少？',[('240℃、4 h、30 atm H2。','euc_cond')]),
 ('test','condition','medium','en','What temperature and residence time optimized joint carbohydrate conversion in the 2020 Eucalyptus grandis integrated biorefinery study?',[('190 °C and 100 min.','euc_yields')]),
 ('test','condition','medium','zh','2021年ChCl/GA水合DES微波实验对糖混合物与桦木锯屑分别采用什么最佳温度和时间？',[('糖混合物160℃、10 min；桦木锯屑170℃、10 min。','ga_cond')]),
 ('test','condition','medium','zh','2018年半纤维素制糠醛综述引用的无催化剂超临界CO2萃取实验，获得68%收率时的木糖浓度、温度、时间、压力和CO2流量是多少？',[('木糖初始浓度4%，230℃，25 min，12 MPa，CO2流量3.6 g/min。','sc_conditions')]),
 ('test','mechanism','medium','zh','根据2018年木质纤维素预处理综述，木质素为什么妨碍纤维素与半纤维素水解？',[('木质素使聚合物结构难以溶解，进而抑制纤维素和半纤维素水解。','recalcitrance')]),
 ('test','mechanism','medium','en','Why did nanosheet-supported Pt perform well in the 2016 aqueous furfural hydrogenation study?',[('Large specific surface area, uniformly dispersed Pt nanoparticles and stronger furfural adsorption.','pt_mech')]),
 ('test','mechanism','medium','zh','2018年环状低聚物制PEF研究为什么不能简单升温熔化原料，论文用什么办法缓解这一问题？',[('环状低聚物混合物熔点约370℃，高于PEF约329℃的降解温度。','pef_problem'),('用高沸点、可移除的惰性液体增塑剂启动反应，再利用生成PEF的自增塑作用。','pef_solution')]),
 ('test','mechanism','medium','zh','2024年Beyond FDCA综述归纳了哪些妨碍HMF从水相回收和稳定保存的性质？',[('HMF亲水且具有极性，不利于从水相回收；又易形成二聚体、低聚物及腐殖质而降解。','hmf_recovery')]),
 ('test','data','easy','zh','2020年Eucalyptus grandis集成炼制论文的最佳糖类共同转化条件下，糠醛与乙酰丙酸收率分别是多少？',[('糠醛55.9%，乙酰丙酸73.6%。','euc_yields')]),
 ('test','comparison','medium','en','In the 2021 DES/MIBK sugar study, how did pure glucose and pure xylose differ in their reported furan-product yields?',[('Glucose gave 10% HMF; xylose gave 62% furfural. These are different products, not two HMF yields.','ga_compare')]),
 ('test','comparison','medium','zh','2021年DES/MIBK研究中，加入ZnCl2与改用果糖这两种尝试对HMF收率分别有什么作用？',[('加入ZnCl2没有提高HMF收率；改用果糖后由10%升至17%。','ga_zn')]),
 ('test','comparison','medium','zh','2018年真实生物质制HMF综述认为，真实原料为何比模型糖更难转化？',[('真实原料分解受纤维素、半纤维素与木质素之间相互作用影响。','real')]),
 ('test','comparison','medium','en','In the 2016 Pt/g-C3N4 study at 100 °C and 1 MPa H2 in water, compare bulk-support and nanosheet-support results after 5 h without confusing yield and selectivity.',[('Bulk-supported 5%Pt gave 60.2% furfuryl-alcohol yield after 5 h.','pt_bulk'),('Nanosheet support achieved complete conversion and >99% furfuryl-alcohol selectivity in 5 h.','pt_sheet'),('The reaction was conducted at 100 °C under 1 MPa H2 in water.','pt_cond')]),
 ('test','comparison','medium','zh','按2018年预处理综述的描述，纤维素在通常水环境与离子液体/NMMO中的溶解性有何差别，水中的例外条件是什么？',[('通常不溶于水，但极低或极高pH是例外；可溶于离子液体及NMMO。','solubility')]),
 ('test','multi_document','hard','zh','比较2021年可回收磷酸分级与2020年Eucalyptus grandis集成炼制：前者怎样回用催化剂，后者在木质素与糖类两步分别使用什么催化剂？',[('磷酸在相分离及水蒸发后回用。','phos_recycle'),('Eucalyptus研究第一步用Pd/C解聚木质素。','euc_order'),('保留糖类的浆料随后用FeCl3处理。','euc_sugars')]),
 ('test','multi_document','hard','en','Compare the solvent strategy and catalyst strategy of the 2021 phosphoric-acid fractionation study and the 2021 ChCl/GA DES/MIBK sugar study; does the latter require an additional catalyst?',[('The phosphoric-acid study uses water/2-MTHF with recyclable phosphoric acid.','phosphoric'),('The other study uses aqueous ChCl/GA DES without an additional catalyst.','ga')]),
 ('test','multi_document','hard','zh','结合2020年Eucalyptus grandis炼制和2021年可回收磷酸分级论文，分别说明两种流程如何利用木质素，并说明各自糖类流的处理方向。',[('Eucalyptus先用Pd/C把木质素转成单体酚。','euc_order'),('其糖类浆料再用FeCl3获得HMF、乙酰丙酸和糠醛。','euc_sugars'),('磷酸流程在水/2-MTHF中分出木质素、纤维素及半纤维素糖。','phosphoric')]),
 ('test','constraint','medium','zh','限定不外加催化剂且处理葡萄糖/木糖混合物：2021年DES/MIBK研究采用什么DES组成及微波反应条件？',[('采用ChCl/GA含水DES，不外加催化剂。','ga'),('糖混合物的最佳微波条件为160℃、10 min。','ga_cond')]),
 ('test','constraint','medium','en','Which catalyst-free hydrothermal route with simultaneous product extraction is described in the 2018 hemicellulose-to-furfural review?',[('Hydrothermal conversion of D-xylose/hemicelluloses with simultaneous supercritical CO2 extraction of furfural.','sc_co2')]),
 ('test','paraphrase','easy','zh','2021年可循环磷酸拆分木质纤维素的工作中，反应液分层后，还需去掉什么才能再次用这份酸？',[('蒸发掉水后可回用磷酸。','phos_recycle')]),
 ('test','paraphrase','easy','zh','2021年糖混合物制呋喃化合物的ChCl/GA工作里，为改善转化又不破坏低共熔介质结构，水加到多少质量百分比？',[('水为32.9 wt%，对应10当量。','ga_water')]),
 ('test','no_answer','medium','zh','2018年瓶级PEF开环聚合论文是否报告其同一批产品在2025年中国市场上市后的食品接触迁移测试原始数据？',[]),

 ('challenge','mechanism','hard','zh','2021年Pt1/Nb2O5-Ov将HMF转为MF的研究中，本来更易被还原的是哪种键，Pt与Nb位点如何分工以实现目标选择性？',[('通常C=O加氢在动力学和热力学上都比C—OH更有利。','sac_difficulty'),('Pt活化H2，Nb位点活化C—OH。','sac_sites'),('单原子催化剂在完全转化时MF选择性超过99%。','sac_result')]),
 ('challenge','mechanism','hard','en','In the 2021 single-atom HMF-to-MF study, explain why reducing HMF to MF with molecular hydrogen is difficult and connect the reported site functions to the selectivity outcome.',[('C=O hydrogenation is kinetically and thermodynamically favored over C–OH.','sac_difficulty'),('Pt activates H2 and Nb activates C-OH.','sac_sites'),('MF selectivity exceeds 99% at complete conversion for the SACs.','sac_result')]),
 ('challenge','constraint','hard','zh','限定以分子氢为还原剂、HMF完全转化并保留醛基得到MF：2021年Nb2O5负载金属研究支持单原子还是纳米金属体系，相关位点分别承担什么作用？',[('支持单原子体系，其MF选择性超过99%，纳米金属体系选择性很差。','sac_result'),('Pt活化H2，Nb活化C—OH。','sac_sites')]),
 ('challenge','comparison','medium','zh','2021年Nb2O5负载金属研究中，单原子与金属纳米催化剂在HMF制MF选择性上有什么差别？',[('单原子催化剂完全转化时MF选择性超过99%，纳米金属催化剂选择性很差。','sac_result')]),
 ('challenge','fact','easy','en','What molecular formula does the 2023 Pd-PdO/ZnSO4 flash-pyrolysis paper give for furfural?',[('C5H4O2.','flash_formula')]),
 ('challenge','data','hard','zh','2023年Pd-PdO/ZnSO4闪速热解在400℃时，C6纤维素及单体、C5木聚糖和甘蔗渣/玉米芯的糠醛收率分别怎样报告？请区分摩尔收率与质量收率。',[('C6纤维素及单体为74–82 mol%，C5木聚糖96 mol%，甘蔗渣和玉米芯23–33 wt%；这些不同基准不能直接比较。','flash_yields')]),
 ('challenge','mechanism','hard','zh','在2023年Pd-PdO/ZnSO4将六碳糖转成五碳糠醛的机制中，PdO层与Pd0核分别做什么，脱去的一碳片段又如何利用？',[('PdO层促进葡萄糖脱去甲醛的Grob裂解；甲醛原位蒸汽重整成H2和CO；Pd0核促进糠醛形成的最后脱水步骤。','flash_roles')]),
 ('challenge','data','easy','zh','2023年Al3+光催化制HMF研究中，70℃时儿茶酚和焦性没食子酸两种配体对应的HMF收率分别是多少？',[('儿茶酚70%，焦性没食子酸67%。','photo_ligands')]),
 ('challenge','multi_document','hard','zh','比较2021年Pt1/Nb2O5-Ov制MF与2023年Pd-PdO/ZnSO4制糠醛两项研究：各自金属/氧化物位点如何分工，不能把哪项活化作用混同？',[('Pt1/Nb2O5-Ov中Pt活化H2、Nb活化C—OH。','sac_sites'),('Pd-PdO/ZnSO4中PdO层负责脱甲醛的Grob裂解，Pd0核促进最终脱水。','flash_roles')]),
 ('challenge','multi_document','hard','en','Contrast the temperature, target furan and reported yield basis for the 2023 fulvic-acid/Al3+ glucose photocatalysis and Pd-PdO/ZnSO4 C6 flash-pyrolysis studies; can their yields be treated as the same product metric?',[('Fulvic-acid/Al3+ photocatalysis gives about 60% HMF at 80 °C.','photo'),('Flash pyrolysis at 400 °C gives 74–82 mol% furfural from C6 cellulose and its monomers. The target products differ.','flash_yields')]),
 ('challenge','multi_document','hard','zh','将2023年Al3+/黄腐酸光催化葡萄糖与2021年Pt1/Nb2O5-Ov氢化HMF串联理解：两篇论文各自报道的原料、目标产物与能量或还原剂条件是什么？是否已证明同一连续串联系统？',[('光催化研究以葡萄糖制HMF，Al3+/黄腐酸吸收太阳光，80℃约60%收率。','photo'),('单原子研究以HMF制MF，使用H2还原剂。两篇各自结果不能证明同一连续串联系统。','sac_result')]),
 ('challenge','no_answer','hard','zh','知识库是否报道将2023年Al3+/黄腐酸葡萄糖光催化与2021年Pt1/Nb2O5-Ov氢化集成在同一连续装置，并给出连续运行1000小时的葡萄糖至MF总碳收率？',[]),
]


def main():
    corpus = [json.loads(s) for s in (ROOT / 'corpus.jsonl').read_text(encoding='utf-8').splitlines()]
    docs = {c['doc_id']: c for c in corpus}
    normalized = {c['doc_id']: norm(c['text']) for c in corpus}
    passages = {}
    for alias, (doc_id, quote) in E.items():
        if norm(quote) not in normalized[doc_id]:
            raise ValueError(f'Quote mismatch: {alias}: {doc_id}')
        passages[alias] = {'doc_id': doc_id, 'source_id': docs[doc_id]['source_id'], 'quote': norm(quote)}
    queries, judgments = [], []
    source_split = {}
    for i, (split, kind, difficulty, language, question, facts) in enumerate(Q, 1):
        qid = f'q_{i:04d}'
        evidence = []
        for j, (fact, alias) in enumerate(facts, 1):
            passage = passages[alias]
            evidence.append({'fact_id': f'f{j}', 'fact': fact, **passage})
            pid = passage['source_id']
            if pid in source_split and source_split[pid] != split:
                raise ValueError('Seed source crosses split: ' + pid)
            source_split[pid] = split
        sources = sorted({f['source_id'] for f in evidence})
        # Explicit attribution prevents unsourced generic definitions from dominating the benchmark.
        query = {'query_id': qid, 'question': question, 'question_type': kind,
                 'difficulty': difficulty, 'answerable': kind != 'no_answer',
                 'language': language, 'split': split,
                 'domain': sorted({topic for alias in [a for _, a in facts]
                                   for topic in [alias.split('_')[0]]}) or ['biomass_furan'],
                 'tags': ([kind] + (['multi_document'] if len(sources) > 1 and kind != 'multi_document' else [])),
                 'required_facts': [f for f, _ in facts],
                 'reference_answer': (' '.join(f for f, _ in facts) if facts else None),
                 'fact_evidence': evidence, 'source_group': sources,
                 'origin': 'AI_authored_from_frozen_evidence',
                 'review_status': 'seed_evidence_checked_full_corpus_review_pending',
                 'notes': '仅允许当前语料证据；未完成全库判断前不得当作完整金标。'}
        if kind == 'no_answer':
            query['notes'] += ' 无答案为待全库核验的假设，不是已证明的结论。'
        queries.append(query)
        # A positive is transferred ONLY if the complete reviewed quotation is present in that
        # source's actual chunk. This is evidence containment, not lexical relevance prediction.
        for doc in corpus:
            supported = [f for f in evidence if f['source_id'] == doc['source_id']
                         and f['quote'] in normalized[doc['doc_id']]]
            if not supported:
                continue  # UNJUDGED, never grade zero.
            grade = 3 if len(supported) == len(evidence) else 2
            judgments.append({'query_id': qid, 'doc_id': doc['doc_id'], 'relevance': grade,
                'method': 'AI_seed_quote_containment_same_source',
                'reason': '完整包含已核验事实原文。' if grade == 3 else '包含部分必要事实，需结合其他证据。',
                'evidence_quotes': [f['quote'] for f in supported],
                'fact_ids': [f['fact_id'] for f in supported],
                'review_status': 'seed_checked', 'human_review': False})
    assert len(queries) == 60
    assert Counter(q['language'] for q in queries) == {'zh': 42, 'en': 18}
    assert Counter(q['difficulty'] for q in queries) == {'easy': 18, 'medium': 30, 'hard': 12}
    assert Counter(q['split'] for q in queries) == {'dev': 12, 'test': 36, 'challenge': 12}
    assert Counter(q['question_type'] for q in queries) == dict(fact=9,definition=6,condition=9,
        mechanism=9,data=6,comparison=6,multi_document=6,constraint=3,paraphrase=3,no_answer=3)
    jsonl(ROOT / 'queries.jsonl', queries)
    jsonl(ROOT / 'seed_judgments.jsonl', judgments)
    write_json(ROOT / 'source_splits.json', {'method': 'group_before_question_authoring',
        'sources': source_split, 'unassigned_sources': sorted({c['source_id'] for c in corpus} - set(source_split)),
        'final_cross_source_leakage_audit': 'pending_exhaustive_labels',
        'shared_retrieval_corpus': True})
    print(json.dumps({'queries': len(queries), 'seed_judgments': len(judgments),
                      'types': dict(Counter(q['question_type'] for q in queries))}))


if __name__ == '__main__':
    main()
