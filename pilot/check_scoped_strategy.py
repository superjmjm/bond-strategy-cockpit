from scoped_strategy import curve_windows, instrument_index, project_curve, source_proposal

def main():
    core="维持30-10y短期在45-50bp区间、供给高峰过后向40-45bp压缩的判断"
    windows=curve_windows(core)
    assert [(w["low_bp"], w["high_bp"]) for w in windows]==[(45,50),(40,45)]
    assert not curve_windows("30-10y利差上周由45-50bp向40-45bp压缩")
    assert not curve_windows("国债期货"+core)
    assert instrument_index("7年期政金债与地方债")==["7年期政金债","7年期地方债"]
    assert set(instrument_index("3y和5y政金债"))=={"3y政金债","5y政金债"}
    view=dict(core=core,spread_windows=windows,source_subject="30-10y利差",citations=[],judgment_id="id")
    cards={}
    project_curve([view],cards)
    assert cards["曲线形态"]["direction"]==1
    cards={}
    project_curve([view,dict(core="预计30-10y利差将走阔")],cards)
    assert not cards

if __name__=="__main__":
    main()
