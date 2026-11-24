"""Read scoped author actions independently of directional voting."""
import re

FORECAST = re.compile(r"预计|预期|有望|可能|大概率|仍有.{0,12}空间|或(?:将|继续|阶段|趋于|小幅)|后续.{0,12}(?:将|可|仍)|建议.{0,15}(?:做平|做陡)")
DIRECTION = re.compile(r"走陡|陡峭化|走阔|压缩|收窄|走平|下行空间")
ARCHIVED = re.compile(r"此前|曾建议|上期|复盘|回顾|昨日|交易盘(?:选择|开始)")
OUTSIDE = re.compile(r"美国|美债|日债|美元|信用债|存单|转债|期货|合约")
TENOR = r"(?<![\d.-])\d+(?:年(?:期)?|[Yy])"
EXPLICIT = re.compile(r"(" + TENOR + r"国债)(?:收益率)?(?:建议|可以|仍宜|以|维持|暂时|继续)*(观望|持有)(?:为主)?")
ELLIPTICAL = re.compile(r"^(" + TENOR + r")(持有|观望)(?:为主)?")
PORTFOLIO = re.compile(r"债券组合|组合久期|组合构建|交易敞口")
CONDITION = re.compile(r"如果|若|倘若|一旦")
OUTLOOK = re.compile(r"债市.{0,6}震荡偏[多空]")
DOMESTIC = re.compile(r"国债|利率债|政金债|地方债")
ACTION = re.compile(r"建议|维持|可(?:以)?关注|适宜|优先")
OBSERVED = re.compile(r"已经|已(?:压缩|收窄|走阔|走平)|本周.{0,10}(?:走阔|收窄)|均走阔")


def curve_intent(fact):
    """Require prospective language beside the directional proposition."""
    predicate = fact.get("predicate") or fact.get("quote", "")
    for clause in re.split(r"[，。；]", predicate):
        if (DIRECTION.search(clause) and FORECAST.search(clause) and not OBSERVED.search(clause)
                and not re.search(r"不(?:会|再|宜|建议)|难以|并非", clause)):
            return True
    return False


def policy_clauses(sentence):
    parts = [p for p in re.split("[，,。；]", sentence) if p]
    if not parts or (ARCHIVED.search(sentence) and not re.search("重申|维持|延续", sentence)):
        return
    # A repeated tenor may inherit 国债 only from the immediately preceding
    # explicit domestic instrument, within the same source sentence.
    for i, part in enumerate(parts):
        if OUTSIDE.search(part) or re.search(r"不建议|不宜|避免|并非|上周|近期已|机构持仓|基金持有|银行持有",part):
            continue
        next_part = parts[i+1] if i+1 < len(parts) else ""
        match = EXPLICIT.search(part)
        if not match and i and EXPLICIT.search(parts[i-1]):
            match = ELLIPTICAL.match(part)
        if match:
            subject = match[1] if "国债" in match[1] else match[1]+"国债"
            action = match[2]
            terms = match[0]
            if re.search(r"补仓|加仓", next_part) and not OUTSIDE.search(next_part):
                terms += "，"+next_part
            yield "标的操作", [[action,subject]], terms
        if PORTFOLIO.search(part) and ACTION.search(part):
            terms = part
            if "组合久期" in part and "中性" in part:
                if CONDITION.match(next_part) and i+2<len(parts) and "久期" in parts[i+2]:
                    terms += "，"+next_part+"，"+parts[i+2]
                yield "组合久期", [["标的","组合久期"]], terms
            elif re.search(r"哑铃|均衡|子弹",part):
                if next_part.startswith(("或","也可")):
                    terms += "，"+next_part
                yield "组合结构", [["标的","债券组合"]], terms
            elif "交易敞口" in part and DOMESTIC.search(part):
                yield "交易配置", [["敞口","交易"]], terms
        if OUTLOOK.search(part) and re.search(r"我们|维持|重申|预计",part):
            extra = next((p for p in parts[i+1:] if "配置机会" in p and not OUTSIDE.search(p)), "")
            if extra:
                yield "市场与配置", [["标的","债市"]], "，".join(parts[i:parts.index(extra,i+1)+1])
        if CONDITION.search(part) and i+2<len(parts):
            following, endpoint = parts[i+1:i+3]
            if "利差" in following and FORECAST.search(following) and "对应" in endpoint and "国债" in endpoint:
                yield "条件利差与标的", [["情景","利差及国债"]], "，".join([part,following,endpoint])
        if re.search(TENOR+r"利率",part) and FORECAST.search(part) and "震荡" in part:
            if i+2<len(parts) and all("%" in p and not OUTSIDE.search(p) for p in parts[i+1:i+3]):
                yield "分期利率展望", [["标的",re.search(TENOR+r"利率",part)[0]]], "，".join(parts[i:i+3])
