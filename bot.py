import os,asyncio,logging
from datetime import datetime,timezone
import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
logging.basicConfig(level=logging.INFO)
log=logging.getLogger(__name__)
TOKEN="8618470619:AAEA94YIgZJZlDaDDGgFNIohxpd6Kl6YMxY"
CHAT="8493385467"
KEY="4e51890e2987488ca88a799c8bd6b1f1"
INTERVAL=300
RR=2.0
SIG=""

def mean(d):return sum(d)/len(d) if d else 0
def ema(p,n):
 if len(p)<n:return p[-1] if p else 0
 k=2/(n+1);r=mean(p[:n])
 for x in p[n:]:r=x*k+r*(1-k)
 return r
def atr(h,l,c,n=14):
 t=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))for i in range(1,len(c))]
 return mean(t[-n:])if t else 0
def rsi(c,n=14):
 if len(c)<n+1:return 50
 g=[max(c[i]-c[i-1],0)for i in range(1,len(c))]
 ls=[max(c[i-1]-c[i],0)for i in range(1,len(c))]
 ag=mean(g[-n:]);al=mean(ls[-n:])
 return 100 if al==0 else 100-(100/(1+ag/al))
def session():
 h=datetime.now(timezone.utc).hour
 if 12<=h<16:return True,"London/NY Overlap"
 if 7<=h<12:return True,"London Session"
 if 16<=h<20:return True,"New York Session"
 return False,"Off-Hours"
async def fetch(s,iv,n=100):
 url=f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={iv}&outputsize={n}&format=JSON&apikey={KEY}"
 async with s.get(url,timeout=aiohttp.ClientTimeout(total=20))as r:
  d=await r.json()
 if"values"not in d:raise RuntimeError(f"{iv}:{d.get('message','err')}")
 rows=sorted(d["values"],key=lambda x:x["datetime"])
 return([float(x["open"])for x in rows],[float(x["high"])for x in rows],[float(x["low"])for x in rows],[float(x["close"])for x in rows])
def signal(m5,m15,h1):
 _,h5,l5,c5=m5;_,h15,l15,c15=m15;_,_,_,ch1=h1
 price=c5[-1];e200=ema(ch1,min(50,len(ch1)-1))
 bias="bull"if price>e200*1.001 else"bear"if price<e200*0.999 else"neutral"
 ok,sess=session()
 if not ok or bias=="neutral":return None
 av=atr(h5,l5,c5);rv=rsi(c15)
 e21=ema(c15,21);e50=ema(c15,50)
 sh=max(h15[-20:]);sl=min(l15[-20:])
 rng=sh-sl
 if bias=="bull":
  zlo=sh-rng*0.786;zhi=sh-rng*0.618
  buf=(zhi-zlo)*0.2;ifib=zlo-buf<=price<=zhi+buf
  eok=e21>e50 and price>e21;rok=40<rv<70
  bok=c15[-1]>sh and c15[-2]<=sh
  sc=sum([ifib,eok,rok,bok])
  if sc>=3 and ifib and eok:
   slp=price-av*1.5;tp=price+(price-slp)*RR
   return{"d":"BUY","p":price,"sl":slp,"tp":tp,"rv":rv,"sess":sess,"sc":sc,"zlo":zlo,"zhi":zhi,"bl":sh,"eok":eok,"rok":rok,"bok":bok,"ifib":ifib}
 if bias=="bear":
  zlo=sl+rng*0.618;zhi=sl+rng*0.786
  buf=(zhi-zlo)*0.2;ifib=zlo-buf<=price<=zhi+buf
  eok=e21<e50 and price<e21;rok=30<rv<60
  bok=c15[-1]<sl and c15[-2]>=sl
  sc=sum([ifib,eok,rok,bok])
  if sc>=3 and ifib and eok:
   slp=price+av*1.5;tp=price-(slp-price)*RR
   return{"d":"SELL","p":price,"sl":slp,"tp":tp,"rv":rv,"sess":sess,"sc":sc,"zlo":zlo,"zhi":zhi,"bl":sl,"eok":eok,"rok":rok,"bok":bok,"ifib":ifib}
 return None
def buildmsg(s):
 ic=s["d"]=="BUY"
 return(f"{'🟢'if ic else'🔴'} *XAUUSD {s['d']} SIGNAL*\n━━━━━━━━━━━━━━━━━━━━━━\n📍 {s['sess']}\n\n💰 *ENTRY:* `{s['p']:.2f}`\n🛑 *SL:* `{s['sl']:.2f}`\n🎯 *TP:* `{s['tp']:.2f}`\n⚖️ *RR:* 1:{RR}\n━━━━━━━━━━━━━━━━━━━━━━\n{'✅'if s['bok']else'⬜'} BOS @ `{s['bl']:.2f}`\n{'✅'if s['ifib']else'⬜'} Fib `{s['zlo']:.2f}-{s['zhi']:.2f}`\n{'✅'if s['eok']else'⬜'} EMA 21/50\n{'✅'if s['rok']else'⬜'} RSI `{s['rv']:.1f}`\n━━━━━━━━━━━━━━━━━━━━━━\n📈 [LIVE CHART](https://www.tradingview.com/chart/?symbol=OANDA:XAUUSD&interval=5)\n⚠️ _Max 1-2% risk. Always use SL._")
async def run():
 global SIG
 bot=Bot(token=TOKEN)
 await bot.send_message(chat_id=CHAT,parse_mode=ParseMode.MARKDOWN,text="🤖 *XAUUSD Bot ONLINE* ✅\n\n📡 Monitoring XAU/USD\n⚙️ BOS + Fib + EMA + RSI\n_Watching for setups..._")
 log.info("Bot online")
 async with aiohttp.ClientSession()as s:
  while True:
   try:
    m5=await fetch(s,"5min")
    await asyncio.sleep(1)
    m15=await fetch(s,"15min")
    await asyncio.sleep(1)
    h1=await fetch(s,"1h",60)
    sig=signal(m5,m15,h1)
    price=m5[3][-1]
    log.info(f"Price:{price:.2f}")
    if sig:
     k=f"{sig['d']}_{int(price)}"
     if k!=SIG:
      await bot.send_message(chat_id=CHAT,text=buildmsg(sig),parse_mode=ParseMode.MARKDOWN)
      SIG=k;log.info(f"Signal:{sig['d']}@{price:.2f}")
    else:log.info("No signal")
   except Exception as e:
    log.error(f"Err:{e}")
    try:await bot.send_message(chat_id=CHAT,parse_mode=ParseMode.MARKDOWN,text=f"⚠️ Error:`{str(e)[:100]}`")
    except:pass
   await asyncio.sleep(INTERVAL)
asyncio.run(run())
