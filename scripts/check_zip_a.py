"""Verify the served A dashboard and its evidence using Chromium."""
import sys,time
from shutil import which
from playwright.sync_api import sync_playwright,expect

def check(url):
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=which("google-chrome") or which("chromium"),args=["--no-sandbox"])
        page=browser.new_page()
        errors=[]
        page.on("pageerror",lambda error:errors.append(error.stack or str(error)))
        for attempt in range(20):
            response=page.request.get(url+'data/snapshot.json?probe='+str(time.time_ns()))
            if response.ok and 'as_of' in response.json(): break
            time.sleep(5)
        else: raise RuntimeError("New dashboard not served")
        docs=response.json()['as_of']
        page.goto(url+'?probe='+str(time.time_ns()))
        for i,s in enumerate(docs):
            page.locator('[data-page=dash]').click()
            page.select_option('#weekSel',str(i))
            expect(page.locator('#c-curve canvas').first).to_be_visible()
            week=s['weeks'][-1]
            assert all(v['week_idx']<=i for v in s['views'])
            from datetime import date,timedelta
            assert all(date.fromisoformat(d)<=date.fromisoformat(week['mon'])+timedelta(days=6) for d in s['dates'])
            rows=page.locator('[data-tenor]')
            assert rows.count()==6
            rows.first.click()
            for t in s['tenors']:
                page.locator('button[data-tenor="'+t['id']+'"]').click()
                expect(page.locator('#c-tw canvas').first).to_be_visible()
                latest={v["broker"]:v for v in s["views"] if v["week_idx"]==i and v["tenor"]==t["id"]}
                for v in latest.values():
                    card=page.locator('.vc[data-vid="'+str(v['id'])+'"]')
                    expect(card).to_be_visible()
                    assert ('仅展示·不计单期限票' in card.inner_text())== (not v['vote_eligible'])
                    card.click()
                    expect(page.locator('#modal')).to_be_visible()
                    assert all(e['pid'] in page.locator('#mbody').inner_text() for e in v['evidence'])
                    page.locator('.mclose').click()
            page.locator('[data-page=broker]').click()
            expect(page.locator('#c-bh canvas').first).to_be_visible()
            print(week['mon'],'cards',s['dash_by_week'][-1]['total_views'],'votes',sum(m['n'] for m in s['dash_by_week'][-1]['matrix']),'market',s['dash_by_week'][-1]['market_date'])
        assert not errors,errors
        browser.close()
if __name__=='__main__':check(sys.argv[1])
