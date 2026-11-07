"""Evidence-based discovery lanes for finished AI entertainment, not general news.

These are transparent title/tag rules, not an AI-generation detector. A provider
category or the keyword used to find a video is never evidence of its content.
"""
import re


LANES = [
    {'id': 'comic', 'title': 'AI 漫剧', 'keywords': ['AI漫剧', '末日漫剧', '悬疑漫剧', 'AI动画短剧'],
     'study': '看开场冲突、角色关系和每集结尾的悬念，优先找能持续连载的设定。'},
    {'id': 'drama', 'title': 'AI 短剧', 'keywords': ['AI真人短剧', 'AI短剧', '真人AIGC'],
     'study': '看人物目标、对白与表演，以及一集如何完成冲突并引出下一集。'},
    {'id': 'pet', 'title': 'AI 萌宠 IP', 'keywords': ['AI萌宠', 'AI小猫', '猫子柒', '云咪', 'JuJu某猪'],
     'study': '看固定主角的外形、性格、生活场景和重复出现的情节，找能连做的角色 IP。'},
    {'id': 'cinema', 'title': 'AI 大片', 'keywords': ['AI剧情大片', 'AI原创电影', '渡骸', '南僵', 'AI中式恐怖'],
     'study': '看世界观、视听风格和关键场面的铺垫，区分完整叙事与单纯的画面展示。'},
]

# Primary creator pages checked on 2026-11-07. These are references, not live
# rankings. Do not attach made-up play counts or infer creator IDs from names.
REFERENCES = [
    {'name': '渡骸', 'lane': 'cinema', 'creator': 'CHunye / 椿野大怪', 'keyword': '渡骸',
     'note': '中式僵尸、跨地域世界观、连续叙事', 'platform': '抖音',
     'url': 'https://www.douyin.com/video/7645676231634996480', 'aweme_id': '7645676231634996480'},
    {'name': '南僵', 'lane': 'cinema', 'creator': '沈不清 AIGC', 'keyword': '南僵',
     'note': '中式恐怖、电影感场面、分集连载', 'platform': '哔哩哔哩',
     'url': 'https://www.bilibili.com/video/BV1uiuZ6TELw/'},
    {'name': '猫子柒', 'lane': 'pet', 'creator': '猫子柒', 'keyword': '猫子柒',
     'note': '固定猫咪角色、田园生活、治愈日常', 'platform': '抖音',
     'url': 'https://www.douyin.com/video/7675600900611143525', 'aweme_id': '7675600900611143525'},
    {'name': '云咪', 'lane': 'pet', 'creator': '云咪猫生哲学', 'keyword': '云咪',
     'note': '猫咪 Vlog、拟人生活、角色陪伴感', 'platform': '抖音',
     'url': 'https://www.douyin.com/video/7633418257768254772', 'aweme_id': '7633418257768254772'},
]

TUTORIAL = re.compile(r'教程|教学|工作流|提示词|保姆级|训练模型|工具测评|工具评测|AI工具|模型测评|卖课|课程|招学员|软件安装|一键生成工具', re.I)
AI = re.compile(r'\bai\b|aigc|人工智能|seedance|seko|可灵|即梦|豆包', re.I)


def validate(value):
    value = str(value or 'all')
    if value not in {'all', 'ai', *(lane['id'] for lane in LANES)}:
        raise ValueError('创作方向无效')
    return value


def classify(item):
    title = str(item.get('title') or '')
    # Chinese hashtags commonly touch "AI", so word-boundaries alone miss them.
    has_ai = bool(AI.search(title) or re.search(r'ai(?:漫|短|真|萌|宠|小猫|小狗|电影|影片|大|剧|原|创|视|动|作|生|中式|国风)|(?:由|使用)\s*ai', title, re.I))
    if TUTORIAL.search(title):
        return {'lanes': [], 'labels': [], 'evidence': [], 'excluded': '教程 / 工具内容'}
    found = []
    if re.search(r'漫剧|动画短剧|动画连续剧', title, re.I):
        found.append('comic')
    if has_ai and re.search(r'短剧|真人|剧集', title) and 'comic' not in found:
        found.append('drama')
    if (has_ai and re.search(r'萌宠|宠物|小猫|猫咪|小狗|拟人动物|动物拟人|哈基米|萌萌|治愈系猫', title)) or re.search(r'猫子柒|云咪|juju某猪|奶猫|奶鼠|奶猴', title, re.I):
        found.append('pet')
    if re.search(r'渡骸|南僵', title) or (has_ai and re.search(r'大片|电影|影视|中式恐怖|科幻短片|奇幻短片|史诗|世界观', title)):
        found.append('cinema')
    if not found and has_ai and re.search(r'短片|原创动画|原创故事|原创视频|AI视频|AI创作|AI原创|脑洞', title, re.I):
        found.append('other')
    evidence = re.findall(r'#[^\s#@，,。!！?？]{1,32}', title)[:8]
    labels = [lane['title'] for lane in LANES if lane['id'] in found]
    if 'other' in found:
        labels.append('其他 AI 创作')
    return {'lanes': found, 'labels': labels, 'evidence': evidence, 'excluded': ''}


def matches(item, focus):
    lanes = item.get('creative', classify(item))['lanes']
    if focus == 'all':
        return True
    return bool(lanes) if focus == 'ai' else focus in lanes


def catalog():
    return {'lanes': LANES, 'references': REFERENCES, 'checked_at': '2026-11-07'}


def opportunities(works):
    """Pick real references across lanes; never invent a story or traffic cause."""
    result, used = [], set()
    ordered = sorted(works, key=lambda r: (r.get('digg_count') is not None, r.get('digg_count') or 0), reverse=True)
    for lane in LANES:
        accounts = set()
        for item in ordered:
            if item['id'] in used or lane['id'] not in item['creative']['lanes']:
                continue
            account = item.get('sec_uid') or item.get('account_name') or item['id']
            if account in accounts:
                continue
            result.append({'lane': lane['id'], 'label': lane['title'], 'work': item,
                           'study': lane['study'], 'basis': '本期已收录作品，按已返回的点赞选择参考'})
            used.add(item['id']); accounts.add(account)
            if len(accounts) == 2:
                break
    return result
