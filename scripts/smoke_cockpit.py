"""Exercise the deployed site in a fresh Chromium session against its published snapshot."""
import argparse
from datetime import date,timedelta
from collections import Counter
from shutil import which
from math import isclose
from statistics import pstdev,mean
import time
from urllib.parse import urljoin,quote
from playwright.sync_api import sync_playwright

def week(day):
    d=date.fromisoformat(day)
    return (d-timedelta(days=d.weekday())).isoformat()

def verify(url, isolated):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=which("google-chrome") or which("chromium"),args=["--no-sandbox"])
        page=browser.new_page()
        errors=[]
        page.on("pageerror",lambda e: errors.append(str(e)))
        for attempt in range(30):
            response=page.request.get(urljoin(url,"snapshot.json")+"?release=anchored-"+str(time.time_ns()))
            if response.ok:
                data=response.json()
                if data.get("isolated") is isolated and data.get("reports"): break
            time.sleep(10)
        else: raise RuntimeError("Expected release snapshot unavailable")
        assert data["buckets"][0]=="整体久期/组合" and len(data["buckets"])==6
        assert data["benchmark"]["benchmark_as_of"] and len(data["benchmark"]["benchmark_yields"])==4
        page.goto(url+"?release="+str(time.time_ns()))
        page.locator("#curve").wait_for()
        assert page.locator("#edition").inner_text()==("隔离验证页" if isolated else "正式看板")
        counts=Counter()
        for day in sorted({week(r["report_date"]) for r in data["reports"]}):
            page.locator("nav a").nth(0).click()
            page.select_option("#week",day)
            rs=[r for r in data["reports"] if week(r["report_date"])==day]
            rows=page.locator("table").first.locator("tbody tr")
            assert rows.count()==6
            for i,bucket in enumerate(data["buckets"]):
                cards=[(r,c) for r in rs for c in r["cards"] if c["bucket"]==bucket]
                voters={}
                for r,c in cards:
                    if c["vote_eligible"]: voters.setdefault(r["broker"],c["direction"])
                ds=list(voters.values())
                cells=rows.nth(i).locator("td").all_text_contents()
                assert cells[0]==bucket and int(cells[-1])==len(cards)
                assert list(map(int,cells[2].split("/")))==[sum((n>0)-(n<0)==j for n in ds) for j in (1,0,-1)]
                if ds: assert isclose(float(cells[1]),mean(ds),abs_tol=.0051) and isclose(float(cells[3]),pstdev(ds),abs_tol=.0051)
                else: assert cells[1]==cells[3]=="—"
                page.goto(url+"#tenor/"+quote(bucket,safe=""))
                page.wait_for_selector("h1:text-is('期限详情')")
                assert page.locator(".pills a").count()==6
                assert page.locator("article").count()==len(cards)
                assert page.locator("article mark").count()==sum(not c["vote_eligible"] for _,c in cards)
                for j,(_,c) in enumerate(cards):
                    article=page.locator("article").nth(j)
                    article.locator("summary").click()
                    assert article.locator("blockquote").count()==len(c["evidence"])
                    assert all(e["pid"] in article.inner_text() and int(e["page"])>=1 for e in c["evidence"])
                page.locator("nav a").nth(0).click()
                rows=page.locator("table").first.locator("tbody tr")
                counts["cards"]+=len(cards);counts["votes"]+=sum(c["vote_eligible"] for _,c in cards)
            page.locator("nav a").nth(2).click()
            page.wait_for_selector("h1:text-is('券商画像')")
            assert page.locator("th").all_inner_texts()==["券商"]+data["buckets"]
            assert not errors,errors
        print("ONLINE_E2E",url,"reports",len(data["reports"]),dict(counts),"benchmark",data["benchmark"]["benchmark_as_of"])
        browser.close()
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("url");p.add_argument("--isolated",action="store_true")
    args=p.parse_args();verify(args.url,args.isolated)
