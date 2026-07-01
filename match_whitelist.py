"""
Step 1 v2: EastMoney 概念名在用户白名单文本中搜索子串匹配
输出 whitelist_mapping.json
"""
import akshare as ak
import json, os, sys
from difflib import SequenceMatcher

DIR = os.path.dirname(os.path.abspath(__file__))

# ── 用户白名单（原始文本） ──
WHITELIST_RAW = '''阿尔茨海默概念 AI PC AI手机 Al语料 阿里巴巴概念 安防BC电池丙烯酸 冰雪产业 比亚迪概念 参股保险 宁德时代概念 白酒概念百度概念参股券商 草甘膦 参股银行 长安汽车概念 长三角一体化 超超临界发电 超导概念 超级电容 超级品牌 车联网(车路协同) 成飞概念 充电桩 宏物经济重组蛋白抽水蓄能 传感器 创投 创新药 储能 存储芯片 大豆 大飞机 代糖概念 国家大基金持股 锂电池概念 电子竞技 电子身份证 低空经济第一代半导体 地下管网 电力物联网 动力电池回收 东数西算(算力 动物逗苗 抖音概念(字节概念) 短刷游戏 独角兽慨念 多模态A 电子组 EDR概念
ERP概念
 三胎概念 ETC 俄乌冲突概念
仿制药一致性评价 钒电池 飞行汽车(eVTOL) 风电 芬太尼 氟化工概念 福建自贸区 富士康概念辅助生殖 钙钛矿电池 肝炎概念 高端装备高股息精选 高铁 高压快充 高压氧舱 共封装光学(CPO) 共同富裕示范区 工业大麻 工业互联网工业母机 股权转让(并购重组) 广东自贸区 光伏概念光刻机 光刻胶 光热发电 固废处理 硅能源 国产操作系统 军工 国企改革 国资云 固态电池金属钴 共享单车 供销社 海工装备 海南自贸区海峡两岸 航空发动机 国产航母 航运概念 亳米波击达 合成生物 核电 黑龙江自贸区 横琴新区 核污染纷治 互联网金融 鸿蒙概念 猴痘概念 化肥换电概念 黄金概念 环氧内烷 华为慨念 华为海思概念股 华为邮鹏 华为欧拉 华为汽车 华为昇腾 沪股通 互联网保险 减肥药 减速器 建筑节能家庭医生 家用电器 京津冀一体化 净水概念 金属回收 金属铅 金属锌 机器人概念 机器视觉 基因测序 军工信息化 军民融合举牌
F5G概念
科创次新股 可降解塑料 可控核聚变 可燃水 空间计算 空气能热泉 快手概念 時电商 垃圾分类 冷链物流 两轮车 粮食概念 量子科技 磷化工流感 露营经济 绿色电力 生态农业 旅游概念 毛发医疗 蚂取集团概念 煤化工概念 煤炭概念 免税店 民爆慨念MnLE 民营医院 納离子电池脑机接口NFT概念 金属 农村电商 农机 农业种植 OLEDPCB概念PEEK材料培育钻石 PET铜箔 啤酒概念 拼多多概念 苹果概念 PM2.5 POE胶膜 PPP概念 汽车拆解概念 汽车电子 汽车热管理汽车芯片期货概念青蒿素 氢能源 禽流感 区块链 染料 燃料电池 人工智能 人形机器人 人造肉 人脸识别 融资融券 柔性屏(折叠屏)柔性直流输电 乳业赛马概念 上海国企改革 上海自贸区 商业航天 深股通 生物疫苗 生物质能发电 深圳国企改革 时空大数据 石墨电极石墨烯食品安全 手机游戏水利 水泥概念 数据安全数据确权数据要素数据中心(AIDC) 数字货币 数字经济 数字李生数字水印数字乡村AI视频ST板块算力租赁 钛白粉概念 太赫兹 碳交易碳纤维 碳中和 特钢概念 特高压 腾讯概念 特色小镇 特斯拉概念天津自贸区 天然气 体育产业同花顺出海50同花顺漂亮100同花顺中特估100 铜缆高速连接 统一大市场 TOPCON电池 土地流转 托育服务 土壤修复金属铜网红经济网络游戏网约车 MCU芯片卫星导航 文化传媒概念 WiFi6 物联网 无人机 无人驾驶 无人零售 污水处理 无线充电 无线耳机物业管理雄安新区乡村振兴 先进封装 消毒剂 消费电子概念 小金属概念 小米概念 小米汽车 细胞免疫治疗 信创 星闪概念 新股与次新股新疆振兴新能原汽车 芯片概念 信托概念 网络安全 新型城镇化 新型工业化 新型烟草(电子烟) 稀土永磁 血氧仪 虚拟电厂 虚拟数字人 虚拟现实牙科医疗 烟草 养鸡 养老概念 央企国企改革 盐湖提锂 眼科医疗 液冷服务器 页岩气一带一路 移动支付 医美概念 英伟达概念 一体化压铸医疗器械概念 有机硅概念 幽门螺杆菌概念 元宇宙 粤港澳大湾区 玉米 云办公 云计算 云游戏 语音技术 预制菜 医药电商 在线教育 中船系化债概念(AMC概念) 智慧城市 智慧灯杆 智慧政务 智能穿戴 智能电网智能家居 智能物流 智能医疗 智能音箱 智能座舱 知识产权保护 职业教育中俄贸易概念 中韩自贸区 中芯国际概念 中字头股票 专精特新 转基因注册制次新股 猪肉 证金持股摘帽 装配式建筑 足球概念 租售同权
自由贸易港
3D打印5G6G概念'''

# ── 已知笔误修正 ──
TYPO_FIXES = {
    '慨念': '概念', '宏物': '宠物', '邮鹏': '鲲鹏',
    '逗苗': '疫苗', '短刷': '短剧', '击达': '雷达',
    '纷治': '防治', '内烷': '丙烷', '李生': '孪生',
    '蚂取': '蚂蚁', '熱泉': '热泵', '新能原': '新能源',
    '亳': '毫', '時电': '跨境电商', '納': '钠',
}

# 清理白名单
cleaned = WHITELIST_RAW
for wrong, right in TYPO_FIXES.items():
    cleaned = cleaned.replace(wrong, right)

print("1. Fetching EastMoney concepts...")
concept_df = ak.stock_board_concept_name_em()
em_names_all = concept_df['板块名称'].tolist()
print(f"   Total: {len(em_names_all)}")

# ── 策略：每个 EastMoney 概念名去 cleaned 文本中搜索 ──
# 为了处理连在一起的情况，对较短的名词（<=5字）要求有空格或边界包围
# 对较长的名词（>5字）直接检查是否为子串

matched = []
unmatched = []

for name in em_names_all:
    # 跳过太泛的概念（成分股>300）
    # We'll handle this later in build_concept_akshare.py

    # 检查是否在白名单文本中
    if name in cleaned:
        matched.append(name)
        continue

    # 尝试不带"概念"后缀的匹配
    if name.endswith('概念') and name[:-2] in cleaned:
        matched.append(name)
        continue

    unmatched.append(name)

print(f"\n2. Direct substring match: {len(matched)} concepts")
print(f"   Unmatched EastMoney concepts: {len(unmatched)}")

# ── 对于未匹配的，尝试短名匹配 ──
# 把 EastMoney 概念名的核心部分提取出来匹配
still_unmatched = []
for name in unmatched:
    core = name
    # 去掉常见后缀
    for suffix in ['概念', '概念股', '板块']:
        if core.endswith(suffix):
            core = core[:-len(suffix)]
    if len(core) >= 3 and core in cleaned:
        matched.append(name)
    else:
        still_unmatched.append(name)

print(f"   After core-name match: {len(matched)} concepts")
print(f"   Still unmatched: {len(still_unmatched)}")

# ── 对于仍未匹配的，用模糊匹配尝试 ──
# 把 still_unmatched 中高分的找出来
fuzzy_added = []
for name in still_unmatched:
    # 尝试 SequenceMatcher 对白名单中的片段匹配
    # 但 cleaner approach: 检查是否为某些已知概念的同义词/缩写
    best = 0
    for matched_name in matched:
        score = SequenceMatcher(None, name, matched_name).ratio()
        if score > best:
            best = score
    if best > 0.85 and best < 1.0:
        # 可能是重复，跳过
        pass

print(f"\n3. Matched concepts:")
for m in sorted(matched):
    print(f"   {m}")

# ── 未匹配的概念中，去掉一些明显不在白名单的 ──
print(f"\n4. Unmatched concepts ({len(still_unmatched)}):")
# 只打印可能与白名单相关的（长度>=3）
for u in sorted(still_unmatched):
    if len(u) >= 3:
        print(f"   {u}")

# ── 保存 ──
out_path = os.path.join(DIR, 'whitelist_mapping.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump({
        'whitelist': sorted(matched),
        'count': len(matched),
        'unmatched': sorted(still_unmatched)
    }, f, ensure_ascii=False, indent=2)
print(f"\n5. Saved whitelist_mapping.json ({len(matched)} whitelisted concepts)")
