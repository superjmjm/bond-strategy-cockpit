"""Regression probes for action scope and historical curve observations."""
from author_policy import curve_intent, policy_clauses


def main():
    def actions(text):
        return [(legs, terms) for _, legs, terms in policy_clauses(text)]

    two = actions("存单具备配置价值，10年国债以观望为主，30年持有，补仓建议逢调整分批操作。")
    assert [legs for legs, _ in two] == [[["观望","10年国债"]], [["持有","30年国债"]]]
    assert "逢调整分批" in two[1][1]
    for text in ["10年美债建议持有。", "上周建议10年国债持有。", "10年国债不建议持有。",
                 "10年国债基金持有规模增加。", "存单仍有价值，30年持有。"]:
        assert not actions(text)
    for predicate in ["均走阔", "已压缩至42BP", "预计不会走陡"]:
        assert not curve_intent({"predicate":predicate})
    assert curve_intent({"predicate":"预计期限利差仍有压缩空间"})


if __name__ == "__main__":
    main()
