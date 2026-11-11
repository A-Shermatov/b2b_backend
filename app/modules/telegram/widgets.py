"""The two message widgets of the bot (Bot API 10.4).

A widget is one HTML document that Telegram draws inside a message in a
private chat with the bot. It runs in a sandbox with no network and no text
inputs — only buttons — and talks to the bot through short requests
(`TelegramWidget.request`, up to 64 bytes) that the bot answers with up to
1024 bytes. The code of a sent widget never changes, so everything that varies
comes either with the message (`chat_data`) or in the answers.

Telegram's limits: 32768 bytes of HTML and 4096 bytes after zlib level 9.
Both widgets are far below; tests keep it that way.

Protocol, feed widget (a supplier reads requests and answers them):
    chat_data          the first request, same shape as the answer to "f:0"
    "f:<index>"     -> {"i","n","id","no","t","c","b","d","k","by","m","p","cur","bf"}
                       or {"n": 0} when the feed is empty
    "o:<request id>:<price>:<days>" -> {"ok": 1}
Protocol, new request widget (a buyer finishes a request started by a message):
    chat_data          {"id","t","c":[[slug,name],...],"g":[city,...]}
    "p:<draft id>:<s|p>:<category slug>:<days>:<city index>" -> {"ok": 1, "no"}
Every answer may instead be {"e": "what went wrong, in words"}.
"""

from __future__ import annotations

import zlib

HTML_LIMIT = 32768
PACKED_LIMIT = 4096
DATA_LIMIT = 1024

_STYLE = """<style>
body{margin:0;padding:12px;font:15px/1.35 system-ui,sans-serif;color:var(--tg-theme-text-color)}
.h{color:var(--tg-theme-hint-color);font-size:13px}
.t{font-weight:600;font-size:17px;margin:4px 0 8px}
.r{display:flex;gap:8px;margin-top:10px}
.r>*{flex:1}
button{border:0;border-radius:10px;height:40px;padding:0 10px;font:600 15px system-ui,sans-serif;background:var(--tg-theme-button-color);color:var(--tg-theme-button-text-color)}
.g{background:var(--tg-theme-secondary-bg-color);color:var(--tg-theme-text-color)}
.k{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:8px}
.w{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}
.w button{height:32px;font-size:13px}
.p{font:600 26px system-ui,sans-serif;margin:4px 0}
.f{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-top:1px solid var(--tg-theme-section-separator-color)}
.f b{font-weight:600;text-align:right}
.x{color:var(--tg-theme-destructive-text-color);font-size:13px;min-height:18px;margin-top:6px}
</style><div id=a></div>"""

# Shared by both widgets: escaping, a button, one request at a time with the
# answer parsed, and one click listener for the whole widget.
_COMMON = r"""var W=window.TelegramWidget,A=document.getElementById('a'),m='',busy=0;
function e(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function b(a,t,c,off){return'<button data-a="'+a+'"'+(c?' class="'+c+'"':'')+(off?' disabled':'')+'>'+t+'</button>'}
function ask(q,ok){if(busy)return;busy=1;m='Отправляем…';draw();W.request(q).then(function(x){busy=0;m='';var o;try{o=JSON.parse(x.result)}catch(_){o=null}
if(!o)m='Бот не ответил, попробуйте ещё раз';else if(o.e)m=o.e;else return ok(o);draw()},function(x){busy=0;m=x&&x.code=='RATE_LIMITED'?'Слишком часто, подождите пару секунд':'Нет связи с ботом, попробуйте ещё раз';draw()})}
function start(){if(!W){A.textContent='Откройте это сообщение в Telegram';return}
A.addEventListener('click',function(ev){var t=ev.target.closest('[data-a]');if(t&&!busy)act(t.dataset.a)});draw()}
"""

FEED_WIDGET = (
    _STYLE
    + "<script>"
    + _COMMON
    + r"""var r=null,v='c',p='',d=7;
try{r=JSON.parse(W.chatData)}catch(_){}
function fmt(s){return s.replace(/\B(?=(\d{3})+$)/g,' ')}
function row(k,x){return x?'<div class=f><span class=h>'+k+'</span><b>'+e(x)+'</b></div>':''}
function draw(){var s;
if(!r||!r.n)s='<div class=t>Заявок пока нет</div><div class=h>Новые заявки покупателей появятся здесь.</div><div class=x>'+e(m)+'</div><div class=r>'+b('u','Обновить')+'</div>';
else if(v=='d')s='<div class=t>Предложение отправлено</div><div class=h>Покупатель увидит его в заявке '+e(r.no)+'. Его ответ придёт сюда и в кабинет на сайте.</div><div class=r>'+b('n','К следующей заявке')+'</div>';
else if(v=='o'){s='<div class=h>Ваше предложение · '+e(r.no)+'</div><div class=t>'+e(r.t)+'</div><div class=h>Цена, '+e(r.cur)+'</div><div class=p>'+(p?fmt(p):'—')+'</div><div class=k>';
for(var i=1;i<10;i++)s+=b('k'+i,i,'g');
s+=b('bs','⌫','g')+b('k0','0','g')+b('z','000','g')+'</div><div class=f><span>Срок, дней</span><span class=r style="margin:0;flex:none">'+b('dm','−','g')+'<b style="min-width:34px;text-align:center;line-height:40px">'+d+'</b>'+b('dp','+','g')+'</span></div><div class=x>'+e(m)+'</div><div class=r>'+b('c','Назад','g')+b('s','Отправить')+'</div>'}
else s='<div class=h>'+e(r.no)+' · '+e(r.k)+(r.p?' · в топе':'')+' · '+(r.i+1)+' из '+r.n+'</div><div class=t>'+e(r.t)+'</div>'+row('Покупатель',r.by)+row('Категория',r.c)+row('Бюджет',r.b)+row('Ответить до',r.d)+'<div class=x>'+e(m)+'</div><div class=r>'+b('pr','◀','g')+b('o',r.m?'Вы ответили':'Ответить','',r.m)+b('n','▶','g')+'</div>';
A.innerHTML=s}
function go(i){ask('f:'+i,function(o){r=o;v='c';p='';draw()})}
function act(a){m='';
if(a=='u')return go(0);
if(a=='n')return go((r.i+1)%r.n);
if(a=='pr')return go((r.i+r.n-1)%r.n);
if(a=='o'){v='o';p=r.bf?String(r.bf):''}
else if(a=='c')v='c';
else if(a=='bs')p=p.slice(0,-1);
else if(a=='z'){if(p&&p.length<7)p+='000'}
else if(a[0]=='k'){if(p.length<9&&(p||a!='k0'))p+=a[1]}
else if(a=='dm')d=Math.max(1,d-1);
else if(a=='dp')d=Math.min(365,d+1);
else if(a=='s'){if(!+p)m='Укажите цену';else return ask('o:'+r.id+':'+p+':'+d,function(){v='d';r.m=1;draw()})}
draw()}
start()</script>"""
)

REQUEST_WIDGET = (
    _STYLE
    + "<script>"
    + _COMMON
    + r"""var D={},k='s',c='',d=7,g=0,done='';
try{D=JSON.parse(W.chatData)||{}}catch(_){}
function chips(list,cur,pre){var s='<div class=w>';for(var i=0;i<list.length;i++)s+=b(pre+i,e(list[i][1]),list[i][0]===cur?'':'g');return s+'</div>'}
function draw(){var s;
if(done)s='<div class=t>Заявка опубликована</div><div class=h>'+e(done)+' · «'+e(D.t)+'». Поставщики уже видят её. Предложения придут сюда и в кабинет на сайте, там же можно добавить бюджет, адрес и файлы.</div>';
else{s='<div class=h>Новая заявка</div><div class=t>'+e(D.t)+'</div><div class=h>Что нужно</div>'+chips([['s','Услуга'],['p','Товар']],k,'k')+'<div class=h>Категория</div>'+chips(D.c||[],c,'c')+'<div class=h>Принимать предложения</div>'+chips([[3,'3 дня'],[7,'7 дней'],[14,'14 дней'],[30,'30 дней']],d,'d');
if(k=='p'){var G=[];for(var i=0;i<(D.g||[]).length;i++)G.push([i,D.g[i]]);s+='<div class=h>Город доставки</div>'+chips(G,g,'g')}
s+='<div class=x>'+e(m)+'</div><div class=r>'+b('s','Опубликовать заявку')+'</div>'}
A.innerHTML=s}
function act(a){m='';var i=+a.slice(1);
if(a[0]=='k')k=i?'p':'s';
else if(a[0]=='c')c=D.c[i][0];
else if(a[0]=='d')d=[3,7,14,30][i];
else if(a[0]=='g')g=i;
else if(a=='s'){if(!c)m='Выберите категорию';else return ask('p:'+D.id+':'+k+':'+c+':'+d+':'+g,function(o){done=o.no||'Заявка';draw()})}
draw()}
start()</script>"""
)


def sizes(html: str) -> tuple[int, int]:
    """(bytes of the HTML, bytes after zlib level 9) — what Telegram checks."""
    raw = html.encode("utf-8")
    return len(raw), len(zlib.compress(raw, 9))


def fits(html: str) -> bool:
    raw, packed = sizes(html)
    return raw <= HTML_LIMIT and packed <= PACKED_LIMIT
