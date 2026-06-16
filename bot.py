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
def killzone():
 h=datetime.now(timezone.utc).hour
 if 7<=h<10:return True,"🇬🇧 London Open Killzone"
 if 10<=h<13:return True,"🔥 London Midday"
 if 13<=h<16:return True,"🇺🇸 NY Open Killzone"
 if 16<=h<20:return True,"🌆 NY Afternoon"
 return False,"😴 Off-Hours"
def bodypct(o,c,h,l):
 rng=h-l
 return 0 if rng==0 else abs(c-o)/rng
 def wickratio(o,c,h,l,bull):
 rng=h-l
 if rng==0:return 0
 if bull:return(o-l)/rng
 if c<o:return(h-o)/rng
 return(h-c)/rng
 url=f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={iv}&outputsize={n}&format=JSON&apikey={KEY}"
 async with s.get(url,timeout=aiohttp.ClientTimeout(total=20))as r:
  d=await r.json()
 if"values"not in d:raise RuntimeError(f"{iv}:{d.get('message','err')}")
 rows=sorted(d["values"],key=lambda x:x["datetime"])
 o=[float(x["open"])for x in rows]
 h=[float(x["high"])for x in rows]
 l=[float(x["low"])for x in rows]
 c=[float(x["close"])for x in rows]
 return o,h,l,c

async def fetchall(s):
 m5=await fetch(s,"5min",100)
 await asyncio.sleep(1)
 m15=await fetch(s,"15min",100)
 await asyncio.sleep(1)
 h1=await fetch(s,"1h",80)
 return m5,m15,h1

def detect_liquidity_grab(h,l,c,lookback=30):
 eqhigh=max(h[-lookback:-5])
 eqlow=min(l[-lookback:-5])
 last_h=h[-2];last_l=l[-2];last_c=c[-2]
 prev_h=h[-3];prev_l=l[-3]
 bull_grab=False;bear_grab=False
 if last_l<eqlow and last_c>eqlow:
  wick=last_c-last_l
  body=abs(last_c-h[-2])
  if wick>body*1.5:bull_grab=True
 if last_h>eqhigh and last_c<eqhigh:
  wick=last_h-last_c
  body=abs(last_c-l[-2])
  if wick>body*1.5:bear_grab=True
 return bull_grab,bear_grab,eqhigh,eqlow

def detect_orderblock(o,h,l,c,bias,lookback=40):
 if bias=="bull":
  for i in range(3,min(lookback,len(c)-2)):
   if c[-i]<o[-i]:
    bullish_after=all(c[-(i-j)]>o[-(i-j)]for j in range(1,3))
    if bullish_after:
     return{"upper":h[-i],"lower":l[-i],"mid":(h[-i]+l[-i])/2,"idx":i}
 if bias=="bear":
  for i in range(3,min(lookback,len(c)-2)):
   if c[-i]>o[-i]:
    bearish_after=all(c[-(i-j)]<o[-(i-j)]for j in range(1,3))
    if bearish_after:
     return{"upper":h[-i],"lower":l[-i],"mid":(h[-i]+l[-i])/2,"idx":i}
 return None

def detect_mss(c,bias,lookback=10):
 if bias=="bull":
  recent_low=min(c[-lookback:-2])
  return c[-1]>c[-2] and c[-2]>recent_low
 if bias=="bear":
  recent_high=max(c[-lookback:-2])
  return c[-1]<c[-2] and c[-2]<recent_high
 return False

def detect_fvg(o,h,l,c,bias,lookback=20):
 for i in range(2,min(lookback,len(c)-1)):
  if bias=="bull":
   if l[-(i-1)]>h[-(i+1)]and(l[-(i-1)]-h[-(i+1)])>0.3:
    return{"upper":l[-(i-1)],"lower":h[-(i+1)],"mid":(l[-(i-1)]+h[-(i+1)])/2}
  if bias=="bear":
   if h[-(i-1)]<l[-(i+1)]and(l[-(i+1)]-h[-(i-1)])>0.3:
    return{"upper":l[-(i+1)],"lower":h[-(i-1)],"mid":(l[-(i+1)]+h[-(i-1)])/2}
 return None

def htf_bias(h1):
 _,_,_,c=h1
 e50=ema(c,min(50,len(c)-1))
 e200=ema(c,min(200,len(c)-1))
 price=c[-1]
 if price>e50 and e50>e200:return"bull"
 if price<e50 and e50<e200:return"bear"
 if price>e200:return"bull"
 if price<e200:return"bear"
 return"neutral"def get_signal(m5,m15,h1):
 o5,h5,l5,c5=m5
 o15,h15,l15,c15=m15
 bias=htf_bias(h1)
 ok,sess=killzone()
 if not ok or bias=="neutral":return None
 price=c5[-1]
 av=atr(h5,l5,c5)
 rv=rsi(c15)
 e21=ema(c15,21)
 e50=ema(c15,50)
 sh=max(h15[-30:])
 sl=min(l15[-30:])
 rng=sh-sl

 bull_grab,bear_grab,eqh,eql=detect_liquidity_grab(h5,l5,c5)
 mss_bull=detect_mss(c5,"bull")
 mss_bear=detect_mss(c5,"bear")
 ob=detect_orderblock(o15,h15,l15,c15,bias)
 fvg=detect_fvg(o5,h5,l5,c5,bias)

 fib_lo=sh-rng*0.786
 fib_hi=sh-rng*0.618
 fib_buf=(fib_hi-fib_lo)*0.25
 in_fib_bull=fib_lo-fib_buf<=price<=fib_hi+fib_buf

 fib_lo2=sl+rng*0.618
 fib_hi2=sl+rng*0.786
 in_fib_bear=fib_lo2-fib_buf<=price<=fib_hi2+fib_buf

 last_wick_bull=wickratio(o5[-2],c5[-2],h5[-2],l5[-2],True)>0.6
 last_wick_bear=wickratio(o5[-2],c5[-2],h5[-2],l5[-2],False)>0.6

 in_ob_bull=ob and ob["lower"]<=price<=ob["upper"]if ob else False
 in_fvg_zone=fvg and fvg["lower"]<=price<=fvg["upper"]if fvg else False

 if bias=="bull":
  ema_ok=e21>e50 and price>e21
  rsi_ok=40<rv<72
  score=sum([
   bull_grab,
   mss_bull,
   in_fib_bull,
   in_ob_bull,
   in_fvg_zone,
   ema_ok,
   rsi_ok,
   last_wick_bull,
  ])
  if score>=4 and (bull_grab or mss_bull) and ema_ok:
   slp=min(price-av*1.2,eql-av*0.3)
   tp=price+(price-slp)*RR
   conf=[]
   if bull_grab:conf.append("✅ Liquidity Grab")
   else:conf.append("⬜ Liquidity Grab")
   if mss_bull:conf.append("✅ Market Structure Shift")
   else:conf.append("⬜ MSS")
   if in_fib_bull:conf.append("✅ Fib 61.8-78.6%")
   else:conf.append("⬜ Fib Zone")
   if in_ob_bull:conf.append("✅ Order Block")
   else:conf.append("⬜ Order Block")
   if in_fvg_zone:conf.append("✅ Fair Value Gap")
   else:conf.append("⬜ FVG")
   if ema_ok:conf.append("✅ EMA Trend")
   else:conf.append("⬜ EMA")
   if rsi_ok:conf.append("✅ RSI "+str(round(rv,1)))
   else:conf.append("⬜ RSI "+str(round(rv,1)))
   if last_wick_bull:conf.append("✅ Wick Rejection")
   else:conf.append("⬜ Wick")
   return{"d":"BUY","p":price,"sl":slp,"tp":tp,"rv":rv,
          "sess":sess,"sc":score,"conf":conf,"eql":eql,"eqh":eqh,
          "ob":ob,"fvg":fvg,"av":av}

 if bias=="bear":
  ema_ok=e21<e50 and price<e21
  rsi_ok=28<rv<60
  score=sum([
   bear_grab,
   mss_bear,
   in_fib_bear,
   in_ob_bull,
   in_fvg_zone,
   ema_ok,
   rsi_ok,
   last_wick_bear,
  ])
  if score>=4 and (bear_grab or mss_bear) and ema_ok:
   slp=max(price+av*1.2,eqh+av*0.3)
   tp=price-(slp-price)*RR
   conf=[]
   if bear_grab:conf.append("✅ Liquidity Grab")
   else:conf.append("⬜ Liquidity Grab")
   if mss_bear:conf.append("✅ Market Structure Shift")
   else:conf.append("⬜ MSS")
   if in_fib_bear:conf.append("✅ Fib 61.8-78.6%")
   else:conf.append("⬜ Fib Zone")
   if in_ob_bull:conf.append("✅ Order Block")
   else:conf.append("⬜ Order Block")
   if in_fvg_zone:conf.append("✅ Fair Value Gap")
   else:conf.append("⬜ FVG")
   if ema_ok:conf.append("✅ EMA Trend")
   else:conf.append("⬜ EMA")
   if rsi_ok:conf.append("✅ RSI "+str(round(rv,1)))
   else:conf.append("⬜ RSI "+str(round(rv,1)))
   if last_wick_bear:conf.append("✅ Wick Rejection")
   else:conf.append("⬜ Wick")
   return{"d":"SELL","p":price,"sl":slp,"tp":tp,"rv":rv,
          "sess":sess,"sc":score,"conf":conf,"eql":eql,"eqh":eqh,
          "ob":ob,"fvg":fvg,"av":av}
 return Nonedef buildmsg(s):
 ic=s["d"]=="BUY"
 dist=abs(s["p"]-s["sl"])
 now=datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
 chart="https://www.tradingview.com/chart/?symbol=OANDA:XAUUSD&interval=5"
 conf_text="\n".join(s["conf"])
 ob_text=f"`{s['ob']['lower']:.2f}-{s['ob']['upper']:.2f}`"if s["ob"]else"None detected"
 fvg_text=f"`{s['fvg']['lower']:.2f}-{s['fvg']['upper']:.2f}`"if s["fvg"]else"None detected"
 return(
  f"{'🟢'if ic else'🔴'} *XAUUSD {s['d']} SIGNAL* {'🟢'if ic else'🔴'}\n"
  f"⚡ *ICT Smart Money Strategy*\n"
  f"━━━━━━━━━━━━━━━━━━━━━━\n"
  f"🕐 {now}\n"
  f"📍 {s['sess']}\n\n"
  f"💰 *ENTRY:* `{s['p']:.2f}`\n"
  f"🛑 *STOP LOSS:* `{s['sl']:.2f}` _({dist:.1f} pts)_\n"
  f"🎯 *TAKE PROFIT:* `{s['tp']:.2f}`\n"
  f"⚖️ *Risk:Reward:* `1:{RR}`\n"
  f"━━━━━━━━━━━━━━━━━━━━━━\n"
  f"📊 *CONFLUENCE* ({s['sc']}/8)\n"
  f"{conf_text}\n"
  f"━━━━━━━━━━━━━━━━━━━━━━\n"
  f"🧱 *Order Block:* {ob_text}\n"
  f"🕳 *FVG:* {fvg_text}\n"
  f"💧 *Liq. Level:* `{s['eql']:.2f}` / `{s['eqh']:.2f}`\n"
  f"📏 *ATR:* `{s['av']:.2f}`\n"
  f"━━━━━━━━━━━━━━━━━━━━━━\n"
  f"📈 *[LIVE CHART]({chart})*\n"
  f"⚠️ _Only risk 1-2% per trade. Respect the SL._"
 )

async def run():
 global SIG
 bot=Bot(token=TOKEN)
 await bot.send_message(chat_id=CHAT,parse_mode=ParseMode.MARKDOWN,
  text=(
   "🤖 *XAUUSD ICT Bot v2 — ONLINE* ✅\n\n"
   "📡 Monitoring: XAU/USD M5 / M15 / H1\n"
   "⚡ Strategy: Liquidity Grab + MSS + OB + FVG + Fib\n"
   "🎯 Target: 4-6 high quality signals/day\n"
   "📊 Sessions: London + New York killzones\n\n"
   "🔍 _Hunting liquidity grabs..._"
  ))
 log.info("ICT Bot v2 online")
 async with aiohttp.ClientSession()as s:
  while True:
   try:
    m5=await fetchall(s)
    m5,m15,h1=m5
    price=m5[3][-1]
    bias=htf_bias(h1)
    ok,sess=killzone()
    log.info(f"Price:{price:.2f} Bias:{bias} Session:{sess} Active:{ok}")
    sig=get_signal(m5,m15,h1)
    if sig:
     k=f"{sig['d']}_{int(price)}"
     if k!=SIG:
      await bot.send_message(chat_id=CHAT,text=buildmsg(sig),
       parse_mode=ParseMode.MARKDOWN,disable_web_page_preview=False)
      SIG=k
      log.info(f"✅ Signal sent:{sig['d']}@{price:.2f} Score:{sig['sc']}/8")
    else:
     log.info(f"No signal. Price:{price:.2f} Bias:{bias}")
   except Exception as e:
    log.error(f"Err:{e}",exc_info=True)
    try:
     await bot.send_message(chat_id=CHAT,parse_mode=ParseMode.MARKDOWN,
      text=f"⚠️ Error:`{str(e)[:100]}`")
    except:pass
   await asyncio.sleep(INTERVAL)

asyncio.run(run())
