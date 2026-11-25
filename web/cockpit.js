"use strict";
const el=id=>document.getElementById(id);
const escapeHTML=x=>String(x??"").replace(/[&<>"']/g,s=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[s]));
const esc=escapeHTML;
const monday=s=>{const d=new Date(s+"T00:00:00Z");d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7);return d.toISOString().slice(0,10)};
const link=(page,key="")=>"#"+page+(key?"/"+encodeURIComponent(key):"");
const table=(headers,rows)=>'<div class="scroll"><table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join("")+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+x+'</td>').join("")+'</tr>').join("")+'</tbody></table></div>';
const panel=(title,body)=>'<section class="panel"><h2>'+esc(title)+'</h2>'+body+'</section>';
const badge='<mark>仅展示·不计单期限票</mark>';
let db;
function latest(cards){
  const out=new Map();
  for(const c of cards) if(c.vote_eligible===true&&!out.has(c.broker))out.set(c.broker,c);
  return [...out.values()];
}
function stats(cards){
  const votes=latest(cards),n=votes.length;
  if(!n)return ["—","0 / 0 / 0","—"];
  const avg=votes.reduce((s,c)=>s+c.direction,0)/n;
  const sd=Math.sqrt(votes.reduce((s,c)=>s+(c.direction-avg)**2,0)/n);
  return [avg.toFixed(2),[1,0,-1].map(v=>votes.filter(c=>Math.sign(c.direction)===v).length).join(" / "),sd.toFixed(2)];
}
const sentiment=c=>c.bucket==="曲线形态"?["显著变陡","偏陡","中性","偏平","显著变平"][c.direction+2]:["看空","偏空","中性","偏多","看多"][c.direction+2];
function card(c){
  const field=(key,v)=>v?'<p><b>'+key+'</b>　'+esc(v)+'</p>':"";
  return '<article><div class="meta">'+esc(c.report_date)+' · '+esc(c.broker)+' · '+esc(c.bucket)+'</div><h3>'+esc(c.title)+'</h3>'+
    '<strong>'+sentiment(c)+'</strong> '+(c.vote_eligible?'':badge)+field("判断",c.logic)+field("操作",c.action)+field("期限与账户",[c.raw_tenor,c.account_note].filter(Boolean).join(" · "))+field("条件",c.cond)+field("观察窗口",c.horizon)+field("风险",c.risk)+field("预测目标",c.target_low!=null&&c.target_high!=null?c.target_low+"–"+c.target_high+" "+c.target_unit:"")+field("点位说明",c.target_note)+
    (c.drivers||[]).map(d=>field(d.layer,d.phrase)).join("")+'<details><summary>原文证据（'+c.evidence.length+' 段）</summary>'+c.evidence.map(e=>'<blockquote><b>物理页 '+esc(e.page)+' · '+esc(e.pid)+'</b><br>'+esc(e.text)+(e.excerpt?'…':'')+'</blockquote>').join("")+'</details></article>';
}
function chart(){
  const canvas=el("curve");if(!canvas)return;
  const keys=["1-3Y","5-7Y","10Y","30Y"],vals=keys.map(k=>db.benchmark.benchmark_yields?.[k]);
  if(!vals.every(Number.isFinite))return;
  const width=canvas.clientWidth||400,height=155,ratio=window.devicePixelRatio||1;
  canvas.width=width*ratio;canvas.height=height*ratio;
  const ctx=canvas.getContext("2d");ctx.scale(ratio,ratio);const lo=Math.min(...vals),hi=Math.max(...vals);
  const pts=vals.map((v,i)=>[32+i*(width-64)/3,103-65*(v-lo)/(hi-lo||1)]);
  ctx.strokeStyle="#21816d";ctx.lineWidth=2;ctx.beginPath();pts.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.stroke();
  ctx.font="13px sans-serif";ctx.textAlign="center";pts.forEach(([x,y],i)=>{ctx.fillStyle="#185b51";ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();ctx.fillText(vals[i].toFixed(2)+"%",x,y-12);ctx.fillText(keys[i],x,137)});
}
function render(){
 const [p="weekly",raw=""]=location.hash.slice(1).split("/");let selected="";try{selected=decodeURIComponent(raw)}catch{}
 const week=el("week").value, reports=db.reports.filter(r=>monday(r.report_date)===week);
 const all=db.reports.flatMap(r=>r.cards.map(c=>({...c,broker:r.broker,title:r.title,report_date:r.report_date})));
 const current=all.filter(c=>monday(c.report_date)===week), prior=all.filter(c=>monday(c.report_date)<week);
 const bucketList=db.buckets;
 const empty='<p class="dim">本周暂无对应观点。</p>';
 const legacy=db.legacy.filter(r=>monday(r.date)===week);
 const archive=(rows)=>rows.length?panel("旧版历史记录",'<details><summary>查看原记录（缺少计票资格标记）</summary>'+rows.map(r=>'<p>'+esc([r.date,r.broker,r.report_title,r.tenor,r.action].join(" · "))+'</p>').join("")+'</details>'):"";
 const pills=(page,names,active)=>'<div class="pills">'+names.map(n=>'<a class="'+(n===active?'sel':'')+'" href="'+link(page,n)+'">'+esc(n)+'</a>').join("")+'</div>';
 const emptyReports=(rs)=>rs.filter(r=>!r.cards.length).map(r=>'<p>'+esc(r.broker+' · '+r.title)+'　'+(r.report_category==="资金流动性周报"?"资金专题·未涉国债策略":"未提及独立利率债策略")+'</p>').join("");
 let html="";
 if(p==="tenor"){
   const b=bucketList.includes(selected)?selected:bucketList[0];
   html='<h1>期限详情</h1>'+pills("tenor",bucketList,b)+(current.filter(c=>c.bucket===b).map(card).join("")||empty);
 }else if(p==="broker"){
   const names=[...new Set([...reports.map(r=>r.broker),...legacy.map(r=>r.broker)])], broker=names.includes(selected)?selected:names[0];
   const matrix=names.map(n=>['<a href="'+link("broker",n)+'">'+esc(n)+'</a>',...bucketList.map(b=>{const cs=current.filter(c=>c.broker===n&&c.bucket===b);
     return cs.map(c=>'<span class="heat '+(c.direction>0?'up':c.direction<0?'down':'')+'">'+sentiment(c)+'</span> '+(c.vote_eligible?'':badge)).join('<br>')||'—'})]);
   html='<h1>券商画像</h1>'+panel("本周观点热力图",table(["券商",...bucketList],matrix))+pills("broker",names,broker)+(current.filter(c=>c.broker===broker).map(card).join("")||empty)+emptyReports(reports.filter(r=>r.broker===broker))+archive(legacy.filter(r=>r.broker===broker));
 }else{
   const rows=[],turns=[];
   for(const b of bucketList){
     const cs=current.filter(c=>c.bucket===b), voters=latest(cs),previous=new Map(latest(prior.filter(c=>c.bucket===b)).map(c=>[c.broker,c]));
     rows.push(['<a href="'+link("tenor",b)+'">'+esc(b)+'</a>',...stats(cs),String(cs.length)]);
     for(const c of voters){const old=previous.get(c.broker);if(old&&c.direction!==old.direction)turns.push([esc(c.broker),esc(b),esc(old.report_date),sentiment(old)+' → '+sentiment(c)])}
   }
   const quote=db.benchmark, values=Object.entries(quote.benchmark_yields||{}).map(([k,v])=>k+' '+v+'%').join('，');
   html='<h1>周度驾驶舱</h1>'+panel("期限观点矩阵",table(["观点桶","共识均分","多 / 中 / 空家数¹","分歧度 σ","观点卡"],rows)+'<small>每桶每家取本周最新可计票观点。¹ 曲线桶依次为看平、中性、看陡。</small>')+'<div class="twocol">'+panel("中债国债收益率曲线",'<canvas id="curve" role="img" aria-label="'+esc(values)+'"></canvas><small>'+esc(quote.benchmark_as_of||"待更新")+' · '+esc(quote.benchmark_note||"")+'</small>')+panel("本周转向",turns.length?table(["券商","观点桶","前次日期","方向变化"],turns):'<p class="dim">本周暂无可比方向变化。</p>')+'</div>'+panel("本周研报",reports.map(r=>'<p><a href="'+link("broker",r.broker)+'">'+esc(r.broker)+'</a> · '+esc(r.title)+' · '+r.cards.length+' 张卡'+(!r.cards.length&&r.report_category==="资金流动性周报"?' · 资金专题·未涉国债策略':'')+'</p>').join("")||empty)+archive(legacy);
 }
 el("content").innerHTML=html;chart();document.querySelectorAll("nav a").forEach(a=>{a.removeAttribute("aria-current");if(a.hash==="#"+(p||"weekly"))a.setAttribute("aria-current","page")});
}
async function start(){const res=await fetch("./snapshot.json",{cache:"no-store"});if(!res.ok)throw Error(String(res.status));db=await res.json();
 el("edition").textContent=db.isolated?"隔离验证页":"正式看板";
 const weeks=[...new Set([...db.reports.map(r=>monday(r.report_date)),...db.legacy.map(r=>monday(r.date))])].sort().reverse();
 el("week").replaceChildren(...weeks.map(s=>new Option(s+" 起",s)));el("week").onchange=render;window.addEventListener("hashchange",render);window.addEventListener("resize",chart);render()}
start().catch(e=>{el("content").textContent="快照读取失败，请刷新重试。";console.error(e)});
